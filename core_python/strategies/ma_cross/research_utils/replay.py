"""Interactive replay helpers for MA Cross research notebooks."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

import numpy as np
import pandas as pd

from core_python.shared.replay_events import replay_events_to_frame

from ._shared import _escape, _widget_output_target, display, show_note, style_table
from .dashboard import calc_drawdown
from ..config import TIMEFRAME, TIMEFRAMES, get_indicator_params, get_symbol_params


EVENT_ORDER = {
    "SIGNAL": 0,
    "SIGNAL_DETECTED": 0,
    "ENTRY": 1,
    "POSITION_OPENED": 1,
    "ORDER_BLOCKED": 2,
    "PARTIAL_TP": 3,
    "PARTIAL_TP_HIT": 3,
    "TRAILING_ACTIVATED": 4,
    "TRAILING_SL_MOVED": 5,
    "EXIT": 6,
    "STOP_LOSS_HIT": 6,
    "TAKE_PROFIT_HIT": 6,
    "REVERSAL_CLOSE": 6,
    "FORCE_CLOSE_END_OF_DATA": 6,
    "DAILY_LOSS_STOP": 7,
    "MAX_DRAWDOWN_STOP": 7,
}


def trades_to_frame(trades: Any) -> pd.DataFrame:
    """Normalize MA Cross trade logs to a DataFrame."""
    if trades is None:
        return pd.DataFrame()
    if isinstance(trades, pd.DataFrame):
        return trades.copy()
    return pd.DataFrame(list(trades))


def build_symbol_replay_events(result: Any, *, include_signals: bool = True) -> pd.DataFrame:
    """Build a time-ordered replay event table for one MA Cross result."""
    engine_events = replay_events_to_frame(getattr(result, "replay_events", []))
    if not engine_events.empty:
        return _event_frame(_normalize_engine_event_frame(engine_events).to_dict("records"))

    symbol = str(getattr(result, "symbol", "") or "")
    rows: list[dict[str, Any]] = []

    signal_data = getattr(result, "signal_data", pd.DataFrame())
    if include_signals and isinstance(signal_data, pd.DataFrame) and "signal" in signal_data.columns:
        idx = _time_index(signal_data)
        signals = signal_data.copy()
        signals["_replay_time"] = pd.to_datetime(idx, errors="coerce")
        for _, row in signals[signals["signal"].fillna(0).astype(int).ne(0)].iterrows():
            direction = int(row.get("signal", 0))
            rows.append(
                {
                    "time": row["_replay_time"],
                    "symbol": symbol,
                    "event_type": "SIGNAL",
                    "side": "BUY" if direction > 0 else "SELL",
                    "price": _maybe_float(row.get("close")),
                    "sl": np.nan,
                    "tp": np.nan,
                    "lot_size": np.nan,
                    "pnl_usd": np.nan,
                    "equity": np.nan,
                    "reason": "close_confirmed",
                    "trade_id": None,
                }
            )

    trades = trades_to_frame(getattr(result, "trades", []))
    for trade_no, trade in trades.reset_index(drop=True).iterrows():
        trade_symbol = str(trade.get("symbol", symbol) or symbol)
        trade_id = f"{trade_symbol}-{trade_no + 1}"
        side = _side_from_trade(trade.get("direction"))
        entry_time = pd.to_datetime(trade.get("entry_time"), errors="coerce")
        exit_time = pd.to_datetime(trade.get("exit_time"), errors="coerce")
        half_time = pd.to_datetime(trade.get("half1_exit_time"), errors="coerce")

        if pd.notna(entry_time):
            rows.append(
                {
                    "time": entry_time,
                    "symbol": trade_symbol,
                    "event_type": "ENTRY",
                    "side": side,
                    "price": _maybe_float(trade.get("entry")),
                    "sl": _maybe_float(trade.get("sl", trade.get("sl_initial"))),
                    "tp": _maybe_float(trade.get("tp")),
                    "lot_size": _maybe_float(trade.get("lot_size")),
                    "pnl_usd": np.nan,
                    "equity": np.nan,
                    "reason": "next_bar_open",
                    "trade_id": trade_id,
                }
            )
        if bool(trade.get("partial_tp_hit", False)) and pd.notna(half_time):
            rows.append(
                {
                    "time": half_time,
                    "symbol": trade_symbol,
                    "event_type": "PARTIAL_TP",
                    "side": side,
                    "price": _maybe_float(trade.get("half1_exit")),
                    "sl": _maybe_float(trade.get("sl", trade.get("sl_initial"))),
                    "tp": _maybe_float(trade.get("tp")),
                    "lot_size": _maybe_float(trade.get("lot_size")),
                    "pnl_usd": np.nan,
                    "equity": np.nan,
                    "reason": "partial_tp",
                    "trade_id": trade_id,
                }
            )
        if pd.notna(exit_time):
            rows.append(
                {
                    "time": exit_time,
                    "symbol": trade_symbol,
                    "event_type": "EXIT",
                    "side": side,
                    "price": _maybe_float(trade.get("exit")),
                    "sl": _maybe_float(trade.get("sl", trade.get("sl_initial"))),
                    "tp": _maybe_float(trade.get("tp")),
                    "lot_size": _maybe_float(trade.get("lot_size")),
                    "pnl_usd": _maybe_float(trade.get("pnl_usd")),
                    "equity": np.nan,
                    "reason": str(trade.get("exit_reason", "") or "closed"),
                    "trade_id": trade_id,
                }
            )

    events = _event_frame(rows)
    equity = getattr(result, "equity", pd.Series(dtype=float))
    if isinstance(equity, pd.Series) and not equity.empty and not events.empty:
        events["equity"] = events["equity"].combine_first(_series_values_at(equity, events["time"]))
    return events


def build_symbol_replay_widget(result: Any, run_config: Mapping[str, Any] | None = None) -> Any:
    """Create an ipywidgets replay control for a completed MA Cross result."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build replay widget: {exc}"

    signal_data = getattr(result, "signal_data", pd.DataFrame())
    equity = getattr(result, "equity", pd.Series(dtype=float))
    events = build_symbol_replay_events(result)
    times = _replay_times(signal_data=signal_data, equity=equity, events=events)
    if not times:
        return "No replay timeline available. Run a backtest with signal/equity data first."

    cfg = dict((run_config or {}).get("replay", {}) if run_config else {})
    slider = widgets.IntSlider(
        value=0,
        min=0,
        max=len(times) - 1,
        step=int(cfg.get("step_bars", 1)),
        description="Time:",
        continuous_update=False,
        layout=widgets.Layout(width="560px"),
    )
    play = widgets.Play(
        value=slider.value,
        min=slider.min,
        max=slider.max,
        step=slider.step,
        interval=int(cfg.get("default_speed_ms", 800)),
        description="Play",
    )
    widgets.jslink((play, "value"), (slider, "value"))
    speed = widgets.IntSlider(
        value=int(cfg.get("default_speed_ms", 800)),
        min=100,
        max=3000,
        step=100,
        description="Speed ms:",
        continuous_update=False,
        layout=widgets.Layout(width="330px"),
    )
    speed.observe(lambda change: setattr(play, "interval", int(change["new"])), names="value")
    lookback = widgets.IntSlider(
        value=int(cfg.get("lookback_bars", 80)),
        min=20,
        max=500,
        step=10,
        description="Bars:",
        continuous_update=False,
        layout=widgets.Layout(width="330px"),
    )
    show_signals = widgets.Checkbox(value=bool(cfg.get("show_signals", True)), description="Signals")
    show_entries = widgets.Checkbox(value=bool(cfg.get("show_entries", True)), description="Entries")
    show_exits = widgets.Checkbox(value=bool(cfg.get("show_exits", True)), description="Exits")
    state_html = widgets.HTML()
    chart_image = widgets.Image(format="png", layout=widgets.Layout(width="100%"))
    events_html = widgets.HTML()

    def render(_: Any = None) -> None:
        current_time = times[slider.value]
        state = replay_state_at(events, current_time, equity=equity)
        state_html.value = _replay_cards_html(
            f"{getattr(result, 'symbol', '')} MA Cross replay",
            {
                "Time": state["current_time"],
                "Equity": state["equity"],
                "Drawdown %": state["drawdown_pct"],
                "Closed": state["closed_trades"],
                "Open": state["open_trades"],
                "PnL": state["pnl_usd"],
            },
        )
        chart_image.value = _render_symbol_replay_png(
            signal_data,
            events,
            current_time=current_time,
            equity=equity,
            symbol=str(getattr(result, "symbol", "") or ""),
            lookback_bars=int(lookback.value),
            show_signals=bool(show_signals.value),
            show_entries=bool(show_entries.value),
            show_exits=bool(show_exits.value),
        )
        events_html.value = _recent_events_html(
            events,
            current_time,
            symbols=[str(getattr(result, "symbol", "") or "")],
            tail=18,
        )

    for control in (slider, lookback, show_signals, show_entries, show_exits):
        control.observe(render, names="value")
    render()
    return widgets.VBox(
        [
            widgets.HTML("<b>MA Cross replay</b>"),
            widgets.HBox([play, slider]),
            widgets.HBox([speed, lookback, show_signals, show_entries, show_exits]),
            state_html,
            chart_image,
            events_html,
        ]
    )


def build_symbol_replay_backtest_widget(
    *,
    symbols: Mapping[str, Any],
    run_symbol_backtest: Any,
    default_config: Mapping[str, Any],
    global_ns: MutableMapping[str, Any] | None = None,
) -> Any:
    """Create a replay-first MA Cross dashboard that runs a fresh backtest."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build replay widget: {exc}"

    ns = global_ns if global_ns is not None else {}
    previous_dashboard = ns.get("_ma_cross_symbol_replay_backtest_dashboard")
    if previous_dashboard is not None and hasattr(previous_dashboard, "close"):
        try:
            previous_dashboard.close()
        except Exception:
            pass
    dashboard_token = object()
    ns["_ma_cross_symbol_replay_backtest_dashboard_token"] = dashboard_token

    symbol_keys = list(symbols.keys())
    default_symbol = str(default_config.get("symbol", symbol_keys[0]))
    if default_symbol not in symbol_keys:
        default_symbol = symbol_keys[0]

    indicator_defaults = get_indicator_params()
    default_indicator_overrides = dict(default_config.get("indicator_overrides") or {})
    default_strategy_overrides = dict(default_config.get("strategy_overrides") or {})
    replay_defaults = dict(default_config.get("replay") or {})

    symbol_widget = widgets.Dropdown(
        options=symbol_keys,
        value=default_symbol,
        description="Symbol:",
        layout=widgets.Layout(width="230px"),
    )
    timeframe_widget = widgets.Dropdown(
        options=TIMEFRAMES,
        value=str(default_config.get("tf", TIMEFRAME)).upper()
        if str(default_config.get("tf", TIMEFRAME)).upper() in TIMEFRAMES
        else TIMEFRAME,
        description="TF:",
        layout=widgets.Layout(width="230px"),
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
        layout=widgets.Layout(width="260px"),
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
        layout=widgets.Layout(width="230px"),
    )
    fast_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("FAST_MA", indicator_defaults["FAST_MA"])),
        description="Fast MA:",
        layout=widgets.Layout(width="230px"),
    )
    slow_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("SLOW_MA", indicator_defaults["SLOW_MA"])),
        description="Slow MA:",
        layout=widgets.Layout(width="230px"),
    )
    atr_period_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("ATR_PERIOD", indicator_defaults["ATR_PERIOD"])),
        description="ATR:",
        layout=widgets.Layout(width="230px"),
    )
    stop_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("atr_stop_mult", 2.0)),
        description="SL ATR:",
        layout=widgets.Layout(width="230px"),
    )
    tp_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("atr_tp_mult", 2.0)),
        description="TP ATR:",
        layout=widgets.Layout(width="230px"),
    )
    trailing_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("trailing_activation", 1.0)),
        description="Trail:",
        layout=widgets.Layout(width="230px"),
    )
    lookback_widget = widgets.IntSlider(
        value=int(replay_defaults.get("lookback_bars", 80)),
        min=20,
        max=500,
        step=10,
        description="Bars:",
        continuous_update=False,
        layout=widgets.Layout(width="330px"),
    )
    speed_widget = widgets.IntSlider(
        value=int(replay_defaults.get("default_speed_ms", 800)),
        min=100,
        max=3000,
        step=100,
        description="Speed ms:",
        continuous_update=False,
        layout=widgets.Layout(width="330px"),
    )
    run_button = widgets.Button(
        description="Run replay backtest",
        button_style="success",
        icon="play",
        layout=widgets.Layout(width="210px"),
    )
    output = widgets.Output()
    state = {"run_active": False}

    def current_config() -> dict[str, Any]:
        indicator_overrides = {
            "MA_TYPE": str(ma_type_widget.value),
            "FAST_MA": int(fast_widget.value),
            "SLOW_MA": int(slow_widget.value),
            "ATR_PERIOD": int(atr_period_widget.value),
        }
        strategy_overrides = {
            "execution_model": str(execution_widget.value),
            "atr_stop_mult": float(stop_widget.value),
            "atr_tp_mult": float(tp_widget.value),
            "trailing_activation": float(trailing_widget.value),
        }
        return {
            "symbol": str(symbol_widget.value),
            "tf": str(timeframe_widget.value),
            "account_mode": str(account_widget.value),
            "initial_balance": float(balance_widget.value),
            "date_from": date_from_widget.value.strip() or None,
            "date_to": date_to_widget.value.strip() or None,
            "max_bars": int(max_bars_widget.value),
            "indicator_overrides": indicator_overrides,
            "strategy_overrides": strategy_overrides,
            "costs": dict(default_config.get("costs") or {}),
            "broker_profile": default_config.get("broker_profile"),
            "collect_events": True,
            "replay": {
                "lookback_bars": int(lookback_widget.value),
                "default_speed_ms": int(speed_widget.value),
                "show_signals": True,
                "show_entries": True,
                "show_exits": True,
            },
        }

    def run_replay_backtest(_: Any = None) -> None:
        if ns.get("_ma_cross_symbol_replay_backtest_dashboard_token") is not dashboard_token:
            return
        if run_button.disabled or state["run_active"]:
            return
        state["run_active"] = True
        run_button.disabled = True
        run_button.description = "Running..."
        output.clear_output(wait=False)
        try:
            with _widget_output_target(output):
                cfg = current_config()
                ns["RUN_CONFIG"] = cfg
                show_note(
                    "Running Replay Backtest",
                    (
                        f"{cfg['symbol']} {cfg['tf']} | {cfg['account_mode']} | "
                        f"{cfg['indicator_overrides']['MA_TYPE'].upper()}"
                        f"{cfg['indicator_overrides']['FAST_MA']}/"
                        f"{cfg['indicator_overrides']['SLOW_MA']}"
                    ),
                )
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
                    collect_events=True,
                )
                ns["result"] = result
                ns["symbol_replay_result"] = result
                ns["SYMBOL"] = cfg["symbol"]
                ns["TIMEFRAME"] = cfg["tf"]
                display(build_symbol_replay_widget(result, cfg))
        except Exception as exc:
            output.append_stdout(f"Replay backtest failed: {exc!r}\n")
        finally:
            state["run_active"] = False
            run_button.disabled = False
            run_button.description = "Run replay backtest"

    run_button.on_click(run_replay_backtest)
    header = widgets.HTML(
        """
        <div style="padding:10px 12px;border:1px solid #30363D;border-radius:6px;background:#F8FAFC">
          <b>MA Cross Replay Backtest Control</b><br>
          <span style="font-size:12px;color:#374151">
          Choose the symbol and MA Cross parameters, run a fresh backtest, then replay the chart.
          </span>
        </div>
        """
    )
    dashboard = widgets.VBox(
        [
            header,
            widgets.HBox([symbol_widget, timeframe_widget, account_widget, execution_widget]),
            widgets.HBox([date_from_widget, date_to_widget, balance_widget, max_bars_widget]),
            widgets.HBox([ma_type_widget, fast_widget, slow_widget, atr_period_widget]),
            widgets.HBox([stop_widget, tp_widget, trailing_widget]),
            widgets.HBox([lookback_widget, speed_widget, run_button]),
            output,
        ]
    )
    ns["_ma_cross_symbol_replay_backtest_dashboard"] = dashboard
    return dashboard


def plot_symbol_replay_frame(
    signal_data: pd.DataFrame,
    events: pd.DataFrame,
    *,
    current_time: Any,
    symbol: str = "",
    lookback_bars: int = 80,
    show_signals: bool = True,
    show_entries: bool = True,
    show_exits: bool = True,
) -> Any:
    """Plot a compact candlestick replay frame with signal/trade markers."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    ts = pd.to_datetime(current_time, errors="coerce")
    frame = _symbol_window(signal_data, ts, lookback_bars)
    fig, ax = plt.subplots(figsize=(16, 7))
    if frame.empty:
        ax.set_title("No price data before selected time")
        plt.show()
        return fig, ax

    xs = np.arange(len(frame))
    width = 0.58
    for pos, row in frame.iterrows():
        open_v = float(row["open"])
        close_v = float(row["close"])
        high_v = float(row["high"])
        low_v = float(row["low"])
        color = "#16A34A" if close_v >= open_v else "#DC2626"
        ax.vlines(pos, low_v, high_v, color=color, linewidth=1.0, alpha=0.85)
        body_low = min(open_v, close_v)
        body_h = max(abs(close_v - open_v), 1e-9)
        ax.add_patch(Rectangle((pos - width / 2, body_low), width, body_h, facecolor=color, edgecolor=color, alpha=0.75))

    if "fast_ma" in frame:
        ax.plot(xs, frame["fast_ma"], color="#2563EB", lw=1.2, label="fast MA")
    if "slow_ma" in frame:
        ax.plot(xs, frame["slow_ma"], color="#F59E0B", lw=1.2, label="slow MA")

    start_time = frame["_replay_time"].iloc[0]
    window_events = events[
        pd.to_datetime(events["time"], errors="coerce").between(start_time, ts, inclusive="both")
    ].copy()
    _scatter_events(ax, frame, window_events, show_signals=show_signals, show_entries=show_entries, show_exits=show_exits)

    labels_pos = np.linspace(0, len(frame) - 1, min(8, len(frame)), dtype=int)
    ax.set_xticks(labels_pos)
    ax.set_xticklabels(
        [pd.Timestamp(frame["_replay_time"].iloc[pos]).strftime("%Y-%m-%d\n%H:%M") for pos in labels_pos],
        fontsize=8,
    )
    ax.set_title(f"{symbol} MA Cross replay".strip())
    ax.grid(alpha=0.22)
    ax.legend(loc="best")
    plt.tight_layout()
    plt.show()
    return fig, ax


def replay_state_at(
    events: pd.DataFrame,
    current_time: Any,
    *,
    equity: pd.Series | None = None,
) -> dict[str, Any]:
    """Summarize MA Cross replay state up to the selected time."""
    ts = pd.to_datetime(current_time, errors="coerce")
    if events is None or events.empty:
        return {
            "current_time": ts,
            "events": 0,
            "signals": 0,
            "closed_trades": 0,
            "open_trades": 0,
            "pnl_usd": 0.0,
            "equity": _series_value_at(equity, ts),
            "drawdown_pct": np.nan,
        }

    past = events[pd.to_datetime(events["time"], errors="coerce").le(ts)].copy()
    entry_types = {"ENTRY", "POSITION_OPENED"}
    exit_types = {"EXIT", "STOP_LOSS_HIT", "TAKE_PROFIT_HIT", "REVERSAL_CLOSE", "FORCE_CLOSE_END_OF_DATA"}
    entries = past[past["event_type"].isin(entry_types)]
    exits = past[past["event_type"].isin(exit_types)]
    if "trade_id" in past.columns and past["trade_id"].notna().any():
        closed_ids = set(exits["trade_id"].dropna().astype(str))
        open_entries = entries[~entries["trade_id"].astype(str).isin(closed_ids)]
    else:
        open_rows = []
        for sym, sym_entries in entries.groupby("symbol"):
            last_entry = sym_entries.iloc[-1]
            sym_exits = exits[exits["symbol"].eq(sym)]
            if sym_exits.empty or pd.Timestamp(last_entry["time"]) > pd.Timestamp(sym_exits["time"].max()):
                open_rows.append(last_entry)
        open_entries = pd.DataFrame(open_rows)

    pnl = pd.to_numeric(exits.get("pnl_usd", pd.Series(dtype=float)), errors="coerce").sum()
    eq = _series_value_at(equity, ts)
    dd = np.nan
    if equity is not None and isinstance(equity, pd.Series) and not equity.empty:
        eq_slice = equity.sort_index().loc[equity.sort_index().index <= ts]
        if not eq_slice.empty:
            dd_series = calc_drawdown(eq_slice)
            dd = float(dd_series.iloc[-1]) if not dd_series.empty else np.nan

    return {
        "current_time": ts,
        "events": int(len(past)),
        "signals": int(past["event_type"].isin({"SIGNAL", "SIGNAL_DETECTED"}).sum()),
        "closed_trades": int(len(exits)),
        "open_trades": int(len(open_entries)),
        "pnl_usd": float(pnl),
        "equity": eq,
        "drawdown_pct": dd,
        "latest_event": past.iloc[-1].to_dict() if not past.empty else None,
    }


def _render_symbol_replay_png(
    signal_data: pd.DataFrame,
    events: pd.DataFrame,
    *,
    current_time: Any,
    equity: pd.Series | None = None,
    symbol: str = "",
    lookback_bars: int = 300,
    show_signals: bool = True,
    show_entries: bool = True,
    show_exits: bool = True,
) -> bytes:
    """Render a flicker-resistant MA Cross replay frame for widgets.Image."""
    from io import BytesIO
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    ts = pd.to_datetime(current_time, errors="coerce")
    fig = Figure(figsize=(16, 10))
    FigureCanvasAgg(fig)
    axes = fig.subplots(3, 1, gridspec_kw={"height_ratios": [3.2, 1.0, 1.1]}, sharex=False)
    price_ax, gap_ax, equity_ax = axes
    df = _symbol_window(signal_data, ts, lookback_bars)

    if df.empty:
        price_ax.set_title("No OHLC data before selected time")
        gap_ax.set_title("No MA gap data")
    else:
        _draw_candlesticks(price_ax, df)
        if "fast_ma" in df.columns:
            price_ax.plot(range(len(df)), df["fast_ma"], color="#2563EB", lw=1.1, label="Fast MA")
        if "slow_ma" in df.columns:
            price_ax.plot(range(len(df)), df["slow_ma"], color="#F59E0B", lw=1.1, label="Slow MA")
        start_time = df["_replay_time"].iloc[0]
        visible_events = events[
            pd.to_datetime(events["time"], errors="coerce").between(start_time, ts)
        ].copy()
        _scatter_replay_events_on_positions(
            price_ax,
            visible_events,
            df["_replay_time"],
            show_signals=show_signals,
            show_entries=show_entries,
            show_exits=show_exits,
        )
        price_ax.axvline(len(df) - 1, color="#60A5FA", lw=1.0, alpha=0.7)
        price_ax.set_xlim(-1, max(len(df), 2))
        _plot_ma_gap_panel(gap_ax, df)

    price_ax.set_title(f"{symbol} MA Cross candlestick replay".strip())
    price_ax.grid(alpha=0.20)
    handles, labels = price_ax.get_legend_handles_labels()
    if handles:
        unique = dict(zip(labels, handles))
        price_ax.legend(unique.values(), unique.keys(), loc="best")
    _plot_equity_slice(equity_ax, equity, ts)
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    return buf.getvalue()


def _display_replay_state(result: Any, events: pd.DataFrame, equity: pd.Series, current_time: Any) -> None:
    ts = pd.to_datetime(current_time, errors="coerce")
    past = events[pd.to_datetime(events["time"], errors="coerce").le(ts)].copy()
    exits = past[past["event_type"].eq("EXIT")]
    eq = _series_value_at(equity, ts)
    frame = pd.DataFrame(
        [
            {"Metric": "Time", "Value": ts},
            {"Metric": "Symbol", "Value": getattr(result, "symbol", "")},
            {"Metric": "Events", "Value": len(past)},
            {"Metric": "Signals", "Value": int(past["event_type"].eq("SIGNAL").sum()) if not past.empty else 0},
            {"Metric": "Closed trades", "Value": len(exits)},
            {"Metric": "Equity", "Value": "-" if pd.isna(eq) else f"{eq:,.2f}"},
        ]
    )
    display(style_table(frame, monospace_value_col="Value"))


def _display_recent_events(events: pd.DataFrame, current_time: Any, tail: int = 18) -> None:
    if events.empty:
        print("No replay events.")
        return
    ts = pd.to_datetime(current_time, errors="coerce")
    view = events[pd.to_datetime(events["time"], errors="coerce").le(ts)].tail(tail)
    cols = ["time", "symbol", "event_type", "side", "price", "pnl_usd", "equity", "reason"]
    display(style_table(view[[c for c in cols if c in view.columns]].reset_index(drop=True)))


def _scatter_events(
    ax: Any,
    frame: pd.DataFrame,
    events: pd.DataFrame,
    *,
    show_signals: bool,
    show_entries: bool,
    show_exits: bool,
) -> None:
    if events.empty:
        return
    time_to_pos = {pd.Timestamp(t): i for i, t in enumerate(frame["_replay_time"])}
    for _, event in events.iterrows():
        t = pd.Timestamp(event["time"])
        if t not in time_to_pos:
            continue
        event_type = str(event.get("event_type", ""))
        if event_type == "SIGNAL" and not show_signals:
            continue
        if event_type == "ENTRY" and not show_entries:
            continue
        if event_type in {"EXIT", "PARTIAL_TP"} and not show_exits:
            continue
        pos = time_to_pos[t]
        price = _maybe_float(event.get("price"))
        if pd.isna(price):
            continue
        side = str(event.get("side", "")).upper()
        if event_type == "SIGNAL":
            marker = "^" if side == "BUY" else "v"
            color = "#38BDF8"
            label = "signal"
        elif event_type == "ENTRY":
            marker = "^" if side == "BUY" else "v"
            color = "#22C55E" if side == "BUY" else "#EF4444"
            label = "entry"
        elif event_type == "PARTIAL_TP":
            marker = "o"
            color = "#A855F7"
            label = "partial TP"
        else:
            marker = "x"
            color = "#111827"
            label = "exit"
        ax.scatter([pos], [price], marker=marker, s=70, color=color, label=label)
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    ax.legend(unique.values(), unique.keys(), loc="best")


def _draw_candlesticks(ax: Any, frame: pd.DataFrame) -> None:
    from matplotlib.patches import Rectangle

    width = 0.62
    for x, row in enumerate(frame.itertuples(index=False)):
        open_v = float(getattr(row, "open"))
        high_v = float(getattr(row, "high"))
        low_v = float(getattr(row, "low"))
        close_v = float(getattr(row, "close"))
        up = close_v >= open_v
        color = "#22C55E" if up else "#EF4444"
        lower = min(open_v, close_v)
        height = max(abs(close_v - open_v), 1e-12)
        ax.vlines(x, low_v, high_v, color=color, linewidth=1.0, alpha=0.95)
        ax.add_patch(
            Rectangle(
                (x - width / 2, lower),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                alpha=0.85,
            )
        )

    tick_count = min(8, len(frame))
    if tick_count:
        positions = np.linspace(0, len(frame) - 1, tick_count).round().astype(int)
        labels = [pd.Timestamp(frame["_replay_time"].iloc[pos]).strftime("%Y-%m-%d\n%H:%M") for pos in positions]
        ax.set_xticks(positions)
        ax.set_xticklabels(labels, fontsize=8)


def _plot_ma_gap_panel(ax: Any, frame: pd.DataFrame) -> None:
    if "ma_gap" in frame.columns:
        values = pd.to_numeric(frame["ma_gap"], errors="coerce").fillna(0.0)
        title = "Fast - slow MA gap"
    elif {"fast_ma", "slow_ma"}.issubset(frame.columns):
        values = pd.to_numeric(frame["fast_ma"], errors="coerce") - pd.to_numeric(frame["slow_ma"], errors="coerce")
        values = values.fillna(0.0)
        title = "Fast - slow MA gap"
    elif "ma_gap_atr" in frame.columns:
        values = pd.to_numeric(frame["ma_gap_atr"], errors="coerce").fillna(0.0)
        title = "MA gap / ATR"
    else:
        ax.set_title("MA gap unavailable")
        ax.grid(alpha=0.20)
        return
    colors = ["#22C55E" if value >= 0 else "#EF4444" for value in values]
    ax.bar(range(len(frame)), values, color=colors, alpha=0.75, width=0.72)
    ax.axhline(0, color="#9CA3AF", lw=0.8)
    ax.set_title(title)
    ax.grid(alpha=0.20)


def _scatter_replay_events_on_positions(
    ax: Any,
    events: pd.DataFrame,
    times: pd.Series,
    *,
    show_signals: bool,
    show_entries: bool,
    show_exits: bool,
) -> None:
    if events.empty or times.empty:
        return
    time_values = pd.to_datetime(times, errors="coerce").reset_index(drop=True)
    for event_types, marker, color, label, enabled in [
        ({"SIGNAL", "SIGNAL_DETECTED"}, "o", "#9CA3AF", "signal", show_signals),
        ({"ENTRY", "POSITION_OPENED"}, "^", "#22C55E", "entry", show_entries),
        ({"ORDER_BLOCKED"}, ">", "#60A5FA", "blocked", show_entries),
        ({"PARTIAL_TP", "PARTIAL_TP_HIT"}, "D", "#F59E0B", "partial TP", show_exits),
        ({"STOP_LOSS_HIT"}, "X", "#DC2626", "stop loss hit", show_exits),
        (
            {"EXIT", "TAKE_PROFIT_HIT", "REVERSAL_CLOSE", "FORCE_CLOSE_END_OF_DATA"},
            "x",
            "#EF4444",
            "exit",
            show_exits,
        ),
    ]:
        if not enabled:
            continue
        rows = events[events["event_type"].isin(event_types)].copy()
        rows = rows[pd.to_numeric(rows["price"], errors="coerce").notna()]
        if rows.empty:
            continue
        xs = [_nearest_position(time_values, t) for t in pd.to_datetime(rows["time"], errors="coerce")]
        ax.scatter(xs, rows["price"], marker=marker, s=58, color=color, label=label, zorder=5)


def _nearest_position(times: pd.Series, ts: pd.Timestamp) -> int:
    if times.empty or pd.isna(ts):
        return 0
    pos = int(times.searchsorted(ts, side="left"))
    if pos <= 0:
        return 0
    if pos >= len(times):
        return len(times) - 1
    prev_delta = abs(ts - times.iloc[pos - 1])
    next_delta = abs(times.iloc[pos] - ts)
    return pos - 1 if prev_delta <= next_delta else pos


def _plot_equity_slice(
    ax: Any,
    equity: pd.Series | None,
    ts: pd.Timestamp,
    *,
    title: str = "Equity and drawdown",
) -> None:
    if equity is None or not isinstance(equity, pd.Series) or equity.empty:
        ax.set_title("No equity data")
        return
    ordered = equity.sort_index()
    eq = ordered.loc[ordered.index <= ts]
    if eq.empty:
        ax.set_title("No equity data before selected time")
        return
    eq.tail(1000).plot(ax=ax, color="#00D4FF", lw=1.6, label="equity")
    dd = calc_drawdown(eq).tail(1000)
    if not dd.empty:
        ax2 = ax.twinx()
        dd.plot(ax=ax2, color="#FF6B6B", lw=1.0, alpha=0.8, label="drawdown %")
        ax2.set_ylabel("Drawdown %")
    ax.set_title(title)
    ax.legend(loc="best")


def _event_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "time",
        "symbol",
        "event_type",
        "side",
        "price",
        "sl",
        "tp",
        "lot_size",
        "pnl_usd",
        "equity",
        "reason",
        "trade_id",
        "metadata",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame = frame.dropna(subset=["time"])
    frame["_event_order"] = frame["event_type"].map(EVENT_ORDER).fillna(99).astype(int)
    return frame.sort_values(["time", "_event_order", "symbol"]).drop(columns=["_event_order"]).reset_index(drop=True)


def _normalize_engine_event_frame(frame: pd.DataFrame) -> pd.DataFrame:
    events = frame.copy()
    if "price" not in events.columns:
        events["price"] = np.nan
    if "entry" in events.columns:
        events["price"] = events["price"].combine_first(events["entry"])
    for col in ["sl", "tp", "lot_size", "pnl_usd", "equity"]:
        if col not in events.columns:
            events[col] = np.nan
    for col in ["side", "reason", "trade_id"]:
        if col not in events.columns:
            events[col] = None
    if "metadata" not in events.columns:
        events["metadata"] = [{} for _ in range(len(events))]
    return events


def _replay_cards_html(title: str, items: Mapping[str, Any]) -> str:
    cards = []
    for label, value in items.items():
        if isinstance(value, pd.Timestamp):
            display_value = str(value)
        elif isinstance(value, (float, int, np.floating, np.integer)):
            display_value = _format_card_value(value)
        else:
            display_value = str(value)
        cards.append(
            f"""
            <div style="min-width:125px;padding:9px 11px;border:1px solid #E5E7EB;border-radius:6px;background:#FFFFFF;">
              <div style="font-size:11px;color:#6B7280;text-transform:uppercase;">{_escape(label)}</div>
              <div style="font-size:17px;font-weight:800;color:#111827;">{_escape(display_value)}</div>
            </div>
            """
        )
    return f"""
        <div style="border:1px solid #D1D5DB;border-radius:8px;background:#F9FAFB;padding:12px 14px;margin:10px 0;">
          <div style="font-size:20px;font-weight:800;color:#111827;margin-bottom:8px;">{_escape(title)}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">{''.join(cards)}</div>
        </div>
        """


def _recent_events_html(
    events: pd.DataFrame,
    current_time: Any,
    *,
    symbols: list[str] | None = None,
    tail: int = 25,
) -> str:
    if events.empty:
        return "<div style='font-size:13px;color:#6B7280;'>No replay events.</div>"
    ts = pd.to_datetime(current_time, errors="coerce")
    view = events[pd.to_datetime(events["time"], errors="coerce").le(ts)].copy()
    if symbols:
        symbols = [sym for sym in symbols if sym]
        if symbols:
            view = view[view["symbol"].isin(symbols)]
    cols = [
        "time",
        "symbol",
        "event_type",
        "side",
        "price",
        "sl",
        "tp",
        "lot_size",
        "pnl_usd",
        "equity",
        "reason",
    ]
    view = view[[c for c in cols if c in view.columns]].tail(tail)
    if view.empty:
        body = "<div style='font-size:13px;color:#6B7280;'>No events before selected time.</div>"
    else:
        display_view = view.copy()
        for col in ["price", "sl", "tp", "lot_size", "pnl_usd", "equity"]:
            if col in display_view.columns:
                display_view[col] = pd.to_numeric(display_view[col], errors="coerce").map(
                    lambda v: "" if pd.isna(v) else _format_table_number(v, decimals=2, strip=False)
                )
        body = display_view.to_html(index=False, border=0, classes="ma-cross-replay-table")
    return f"""
    <div style="margin-top:10px;">
      <div style="font-size:16px;font-weight:700;color:#111827;margin-bottom:6px;">Recent replay events</div>
      <style>
        .ma-cross-replay-table {{
          border-collapse: collapse;
          width: 100%;
          font-size: 12px;
          color: #111827;
        }}
        .ma-cross-replay-table th {{
          background: #E5E7EB;
          padding: 6px 8px;
          text-align: left;
          border-bottom: 1px solid #D1D5DB;
        }}
        .ma-cross-replay-table td {{
          padding: 5px 8px;
          border-bottom: 1px solid #F1F5F9;
        }}
      </style>
      <div style="overflow-x:auto;">{body}</div>
    </div>
    """


def _format_card_value(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
    except Exception:
        pass
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        abs_value = abs(float(value))
        decimals = 2 if abs_value >= 1 else 4
        return f"{float(value):,.{decimals}f}".rstrip("0").rstrip(".")
    return str(value)


def _format_table_number(value: Any, *, decimals: int = 2, strip: bool = True) -> str:
    try:
        if value is None or pd.isna(value):
            return ""
        out = f"{float(value):,.{decimals}f}"
        return out.rstrip("0").rstrip(".") if strip else out
    except Exception:
        return str(value)


def _replay_times(*, signal_data: pd.DataFrame, equity: pd.Series, events: pd.DataFrame) -> list[pd.Timestamp]:
    times: list[pd.Timestamp] = []
    if isinstance(signal_data, pd.DataFrame) and not signal_data.empty:
        times.extend(pd.to_datetime(_time_index(signal_data), errors="coerce").dropna().tolist())
    if isinstance(equity, pd.Series) and not equity.empty:
        times.extend(pd.to_datetime(equity.index, errors="coerce").dropna().tolist())
    if isinstance(events, pd.DataFrame) and not events.empty:
        times.extend(pd.to_datetime(events["time"], errors="coerce").dropna().tolist())
    return sorted(set(pd.Timestamp(t) for t in times))


def _symbol_window(signal_data: pd.DataFrame, ts: pd.Timestamp, lookback_bars: int) -> pd.DataFrame:
    if not isinstance(signal_data, pd.DataFrame) or signal_data.empty:
        return pd.DataFrame()
    frame = signal_data.copy()
    frame["_replay_time"] = pd.to_datetime(_time_index(frame), errors="coerce")
    frame = frame.dropna(subset=["_replay_time"]).sort_values("_replay_time")
    return frame[frame["_replay_time"].le(ts)].tail(max(int(lookback_bars), 1)).reset_index(drop=True)


def _time_index(frame: pd.DataFrame) -> pd.Index:
    if isinstance(frame.index, pd.DatetimeIndex):
        return frame.index
    if "BarTime" in frame.columns:
        return pd.DatetimeIndex(pd.to_datetime(frame["BarTime"], errors="coerce"))
    return pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))


def _side_from_trade(direction: Any) -> str:
    try:
        d = int(direction)
        return "BUY" if d > 0 else "SELL"
    except Exception:
        text = str(direction or "").upper()
        return text if text in {"BUY", "SELL"} else ""


def _maybe_float(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        out = float(value)
        return out if np.isfinite(out) else float("nan")
    except Exception:
        return float("nan")


def _series_value_at(series: pd.Series | None, ts: Any) -> float:
    if series is None or not isinstance(series, pd.Series) or series.empty:
        return float("nan")
    return _series_values_at(series, pd.Series([pd.to_datetime(ts, errors="coerce")])).iloc[0]


def _series_values_at(series: pd.Series, times: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(np.nan, index=times.index)
    ordered = series.sort_index()
    idx = pd.to_datetime(ordered.index, errors="coerce")
    ordered = pd.Series(ordered.to_numpy(dtype=float), index=idx).dropna()
    if ordered.empty:
        return pd.Series(np.nan, index=times.index)
    query = pd.to_datetime(times, errors="coerce")
    positions = ordered.index.searchsorted(query, side="right") - 1
    values = [float(ordered.iloc[pos]) if pos >= 0 else np.nan for pos in positions]
    return pd.Series(values, index=times.index)


__all__ = [
    "build_symbol_replay_backtest_widget",
    "build_symbol_replay_events",
    "build_symbol_replay_widget",
    "plot_symbol_replay_frame",
    "replay_state_at",
    "trades_to_frame",
]
