"""Replay-style research views for Combo backtest results.

These helpers build a lightweight event timeline from completed backtest
results. They do not change signal or execution behavior; the replay is a
visual/debug layer over the existing trade log, signal frame, and equity curve.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

import numpy as np
import pandas as pd

from ._shared import (
    _display_html,
    _display_obj,
    _escape,
    _format_card_value,
    _format_table_number,
    _show_plot,
    _table_formatters,
    _time_index,
    _widget_output_target,
    calc_drawdown,
)
from .dashboard import trades_to_frame
from core_python.strategies.combo.engines.replay_events import replay_events_to_frame
from core_python.strategies.combo.config import OPTIMIZATION, get_indicator_params, get_symbol_params


EVENT_ORDER = {
    "SIGNAL": 0,
    "SIGNAL_DETECTED": 0,
    "PENDING_CREATED": 1,
    "PENDING_FILLED": 2,
    "ENTRY": 1,
    "POSITION_OPENED": 3,
    "PARTIAL_TP": 4,
    "PARTIAL_TP_HIT": 4,
    "TRAILING_ACTIVATED": 5,
    "TRAILING_SL_MOVED": 6,
    "EXIT": 7,
    "STOP_LOSS_HIT": 7,
    "TAKE_PROFIT_HIT": 7,
    "REVERSAL_CLOSE": 7,
    "FORCE_CLOSE_END_OF_DATA": 7,
    "PENDING_EXPIRED": 8,
    "DAILY_LOSS_STOP": 9,
    "MAX_DRAWDOWN_STOP": 9,
}


def build_symbol_replay_events(result: Any, *, include_signals: bool = True) -> pd.DataFrame:
    """Build a time-ordered replay event table for one SymbolBacktestResult."""
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
        signal_rows = signals[signals["signal"].fillna(0).astype(int).ne(0)]
        for _, row in signal_rows.iterrows():
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
                    "reason": "signal",
                    "trade_id": None,
                }
            )

    trades = trades_to_frame(getattr(result, "trades", []))
    for trade_no, trade in trades.reset_index(drop=True).iterrows():
        trade_symbol = str(trade.get("symbol", symbol) or symbol)
        trade_id = f"{trade_symbol}-{trade_no + 1}"
        side = str(trade.get("direction", "") or "")
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
                    "sl": _maybe_float(trade.get("sl_initial")),
                    "tp": _maybe_float(trade.get("tp")),
                    "lot_size": _maybe_float(trade.get("lot_size")),
                    "pnl_usd": np.nan,
                    "equity": np.nan,
                    "reason": "filled",
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
                    "price": _maybe_float(trade.get("half1_exit", trade.get("tp"))),
                    "sl": _maybe_float(trade.get("sl_initial")),
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
                    "sl": _maybe_float(trade.get("sl_initial")),
                    "tp": _maybe_float(trade.get("tp")),
                    "lot_size": _maybe_float(trade.get("lot_size")),
                    "pnl_usd": _maybe_float(trade.get("pnl_usd")),
                    "equity": _maybe_float(trade.get("equity")),
                    "reason": str(trade.get("exit_reason", "") or "closed"),
                    "trade_id": trade_id,
                }
            )

    events = _event_frame(rows)
    equity = getattr(result, "equity", pd.Series(dtype=float))
    if isinstance(equity, pd.Series) and not equity.empty and not events.empty:
        events["equity"] = events["equity"].combine_first(_series_values_at(equity, events["time"]))
    return events


def build_portfolio_replay_events(portfolio_result: Any, *, include_signals: bool = True) -> pd.DataFrame:
    """Build a replay event table for all symbols in a PortfolioBacktestResult."""
    engine_events = replay_events_to_frame(getattr(portfolio_result, "replay_events", []))
    if not engine_events.empty:
        events = _event_frame(_normalize_engine_event_frame(engine_events).to_dict("records"))
        combined = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
        if isinstance(combined, pd.Series) and not combined.empty and not events.empty:
            events["portfolio_equity"] = _series_values_at(combined, events["time"])
        return events

    symbol_results = getattr(portfolio_result, "symbol_results", {}) or {}
    frames = [
        build_symbol_replay_events(result, include_signals=include_signals)
        for result in symbol_results.values()
    ]
    if frames:
        events = _event_frame(pd.concat(frames, ignore_index=True).to_dict("records"))
    else:
        proxy = type("ReplayProxy", (), {})()
        proxy.symbol = "PORTFOLIO"
        proxy.signal_data = pd.DataFrame()
        proxy.trades = getattr(portfolio_result, "trades", [])
        proxy.equity = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
        events = build_symbol_replay_events(proxy, include_signals=False)

    combined = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
    if isinstance(combined, pd.Series) and not combined.empty and not events.empty:
        events["portfolio_equity"] = _series_values_at(combined, events["time"])
    return events


def replay_state_at(
    events: pd.DataFrame,
    current_time: Any,
    *,
    equity: pd.Series | None = None,
) -> dict[str, Any]:
    """Summarize replay state up to the selected time."""
    if events is None or events.empty:
        return {
            "current_time": pd.to_datetime(current_time, errors="coerce"),
            "events": 0,
            "signals": 0,
            "closed_trades": 0,
            "open_trades": 0,
            "pnl_usd": 0.0,
            "equity": _series_value_at(equity, current_time),
            "drawdown_pct": np.nan,
        }

    ts = pd.to_datetime(current_time, errors="coerce")
    past = events[pd.to_datetime(events["time"], errors="coerce") <= ts].copy()
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


def build_symbol_replay_widget(
    result: Any,
    run_config: Mapping[str, Any] | None = None,
) -> Any:
    """Create an ipywidgets replay control for one symbol backtest result."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build replay widget: {exc}"

    signal_data = getattr(result, "signal_data", pd.DataFrame())
    events = build_symbol_replay_events(result)
    equity = getattr(result, "equity", pd.Series(dtype=float))
    times = _replay_times(signal_data=signal_data, equity=equity, events=events)
    if len(times) == 0:
        return "No replay timeline available. Run a backtest with signal/equity data first."

    cfg = dict((run_config or {}).get("replay", {}) if run_config else {})
    start_index = len(times) - 1 if bool(cfg.get("start_at_end", False)) else 0
    slider = widgets.IntSlider(
        value=start_index,
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
        interval=int(cfg.get("default_speed_ms", 300)),
        description="Play",
    )
    widgets.jslink((play, "value"), (slider, "value"))
    speed = widgets.IntSlider(
        value=int(cfg.get("default_speed_ms", 300)),
        min=100,
        max=3000,
        step=100,
        description="Speed ms:",
        continuous_update=False,
        layout=widgets.Layout(width="320px"),
    )
    speed.observe(lambda change: setattr(play, "interval", int(change["new"])), names="value")
    lookback = widgets.IntSlider(
        value=int(cfg.get("lookback_bars", 50)),
        min=20,
        max=500,
        step=10,
        description="Bars:",
        continuous_update=False,
        layout=widgets.Layout(width="320px"),
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
            f"{getattr(result, 'symbol', '')} candlestick replay",
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
            widgets.HTML("<b>Symbol replay</b>"),
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
    """Create a replay-first control panel that runs a fresh symbol backtest."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build replay widget: {exc}"

    ns = global_ns if global_ns is not None else {}
    previous_dashboard = ns.get("_combo_symbol_replay_backtest_dashboard")
    if previous_dashboard is not None and hasattr(previous_dashboard, "close"):
        try:
            previous_dashboard.close()
        except Exception:
            pass
    dashboard_token = object()
    ns["_combo_symbol_replay_backtest_dashboard_token"] = dashboard_token

    symbol_keys = list(symbols.keys())
    if not symbol_keys:
        return "No Combo symbols are configured."

    indicator_defaults = get_indicator_params()
    replay_defaults = dict(default_config.get("replay") or {})
    default_indicator_overrides = dict(default_config.get("indicator_overrides") or {})
    default_symbol_overrides = dict(default_config.get("symbol_overrides") or {})
    default_strategy_overrides = dict(default_config.get("strategy_overrides") or {})
    ktp_values = list(replay_defaults.get("ktp_values") or OPTIMIZATION["symbol"]["ktp_values"])
    ktp_options = [(f"{float(v):.3f}".rstrip("0").rstrip("."), float(v)) for v in ktp_values]
    default_symbol = str(default_config.get("symbol", symbol_keys[0]))
    if default_symbol not in symbol_keys:
        default_symbol = symbol_keys[0]

    def symbol_defaults(symbol: str) -> dict[str, Any]:
        return get_symbol_params(symbol)

    sym_default = symbol_defaults(default_symbol)
    default_ktp = float(default_symbol_overrides.get("ktp", sym_default.get("ktp", ktp_options[0][1])))
    if default_ktp not in [v for _, v in ktp_options]:
        default_ktp = min((v for _, v in ktp_options), key=lambda v: abs(v - default_ktp))

    symbol_widget = widgets.Dropdown(
        options=symbol_keys,
        value=default_symbol,
        description="Symbol:",
        layout=widgets.Layout(width="230px"),
    )
    account_widget = widgets.Dropdown(
        options=["standard", "ftmo"],
        value=str(default_config.get("account_mode", "standard")),
        description="Account:",
        layout=widgets.Layout(width="230px"),
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
    ktp_widget = widgets.Dropdown(
        options=ktp_options,
        value=default_ktp,
        description="KTP:",
        layout=widgets.Layout(width="230px"),
    )
    x_widget = widgets.FloatText(
        value=float(default_symbol_overrides.get("x", sym_default.get("x", 0.0))),
        description="X:",
        layout=widgets.Layout(width="230px"),
    )
    default_min_rr = default_indicator_overrides.get("MIN_RR", indicator_defaults.get("MIN_RR"))
    min_rr_widget = widgets.Text(
        value="" if default_min_rr is None else str(default_min_rr),
        description="Min RR:",
        placeholder="blank = off",
        layout=widgets.Layout(width="230px"),
    )
    ma_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("MA_PERIOD", sym_default.get("ma_period", indicator_defaults["MA_PERIOD"]))),
        description="MA:",
        layout=widgets.Layout(width="230px"),
    )
    atr_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("ATR_PERIOD", indicator_defaults["ATR_PERIOD"])),
        description="ATR:",
        layout=widgets.Layout(width="230px"),
    )
    macd_fast_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("MACD_FAST", indicator_defaults["MACD_FAST"])),
        description="MACD F:",
        layout=widgets.Layout(width="230px"),
    )
    macd_slow_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("MACD_SLOW", indicator_defaults["MACD_SLOW"])),
        description="MACD S:",
        layout=widgets.Layout(width="230px"),
    )
    macd_signal_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("MACD_SIGNAL", indicator_defaults["MACD_SIGNAL"])),
        description="MACD Sig:",
        layout=widgets.Layout(width="230px"),
    )
    trailing_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("trailing_activation", sym_default.get("trailing_activation", 1.0))),
        description="Trail:",
        layout=widgets.Layout(width="230px"),
    )
    ttl_widget = widgets.IntText(
        value=int(default_strategy_overrides.get("pending_ttl_bars", 3)),
        description="TTL:",
        layout=widgets.Layout(width="230px"),
    )
    lookback_widget = widgets.IntSlider(
        value=int(replay_defaults.get("lookback_bars", 50)),
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

    def parse_optional_float(value: Any) -> float | None:
        raw = str(value or "").strip().lower()
        if raw in {"", "none", "off", "null"}:
            return None
        return float(raw)

    def refresh_symbol_defaults(change: Any = None) -> None:
        if change is not None and change.get("name") != "value":
            return
        params = symbol_defaults(str(symbol_widget.value))
        x_widget.value = float(params.get("x", x_widget.value))

    def current_config() -> dict[str, Any]:
        min_rr = parse_optional_float(min_rr_widget.value)
        indicator_overrides = {
            "MA_PERIOD": int(ma_widget.value),
            "MACD_FAST": int(macd_fast_widget.value),
            "MACD_SLOW": int(macd_slow_widget.value),
            "MACD_SIGNAL": int(macd_signal_widget.value),
            "ATR_PERIOD": int(atr_widget.value),
            "KTP": float(ktp_widget.value),
            "X": float(x_widget.value),
            "MIN_RR": min_rr,
        }
        symbol_overrides = {
            "x": float(x_widget.value),
            "ktp": float(ktp_widget.value),
            "ma_period": int(ma_widget.value),
            "trailing_activation": float(trailing_widget.value),
        }
        strategy_overrides = {
            "trailing_activation": float(trailing_widget.value),
            "pending_ttl_bars": int(ttl_widget.value),
            "min_rr": min_rr,
        }
        return {
            "symbol": str(symbol_widget.value),
            "account_mode": str(account_widget.value),
            "initial_balance": float(balance_widget.value),
            "date_from": date_from_widget.value.strip() or None,
            "date_to": date_to_widget.value.strip() or None,
            "max_bars": int(max_bars_widget.value),
            "indicator_overrides": indicator_overrides,
            "symbol_overrides": symbol_overrides,
            "strategy_overrides": strategy_overrides,
            "collect_events": True,
            "replay": {
                "lookback_bars": int(lookback_widget.value),
                "default_speed_ms": int(speed_widget.value),
                "show_signals": True,
                "show_entries": True,
                "show_exits": True,
            },
            "export_report": bool(default_config.get("export_report", False)),
        }

    def run_replay_backtest(_: Any = None) -> None:
        if ns.get("_combo_symbol_replay_backtest_dashboard_token") is not dashboard_token:
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
                _display_html(
                    f"""
                    <div style="padding:10px 12px;border-left:4px solid #06B6D4;background:#F8FAFC;margin:8px 0 12px 0;">
                      <div style="font-weight:700;color:#111827;">Running Replay Backtest</div>
                      <div style="font-size:13px;color:#374151;">
                        {cfg['symbol']} | {cfg['account_mode']} | KTP {cfg['indicator_overrides']['KTP']} |
                        Min RR {_escape(str(cfg['indicator_overrides']['MIN_RR'] if cfg['indicator_overrides']['MIN_RR'] is not None else 'off'))}
                      </div>
                    </div>
                    """
                )
                result = run_symbol_backtest(
                    cfg["symbol"],
                    init_eq=cfg["initial_balance"],
                    account_mode=cfg["account_mode"],
                    date_from=cfg["date_from"],
                    date_to=cfg["date_to"],
                    max_bars=cfg["max_bars"],
                    indicator_overrides=cfg["indicator_overrides"],
                    symbol_overrides=cfg["symbol_overrides"],
                    strategy_overrides=cfg["strategy_overrides"],
                    collect_events=True,
                )
                ns["result"] = result
                ns["symbol_replay_result"] = result
                ns["SYMBOL"] = cfg["symbol"]
                ns["ACCOUNT_MODE"] = cfg["account_mode"]
                replay = build_symbol_replay_widget(result, cfg)
                _display_obj(replay)
        except Exception as exc:
            with _widget_output_target(output):
                _display_html(f'<pre style="color:red">Replay backtest failed:<br>{_escape(repr(exc))}</pre>')
        finally:
            state["run_active"] = False
            run_button.disabled = False
            run_button.description = "Run replay backtest"

    symbol_widget.observe(refresh_symbol_defaults, names="value")
    run_button.on_click(run_replay_backtest)

    header = widgets.HTML(
        """
        <div style="padding:10px 12px;border:1px solid #30363D;border-radius:6px;background:#F8FAFC">
          <b>Replay Backtest Control</b><br>
          <span style="font-size:12px;color:#374151">
          Choose the symbol and parameters, run a fresh Combo backtest, then replay the actual engine events.
          </span>
        </div>
        """
    )
    dashboard = widgets.VBox(
        [
            header,
            widgets.HBox([symbol_widget, account_widget, date_from_widget, date_to_widget]),
            widgets.HBox([balance_widget, max_bars_widget, ktp_widget, x_widget]),
            widgets.HBox([min_rr_widget, ma_widget, atr_widget]),
            widgets.HBox([macd_fast_widget, macd_slow_widget, macd_signal_widget]),
            widgets.HBox([trailing_widget, ttl_widget, lookback_widget, speed_widget]),
            run_button,
            output,
        ]
    )
    ns["_combo_symbol_replay_backtest_dashboard"] = dashboard
    return dashboard


def build_portfolio_replay_widget(
    portfolio_result: Any,
    run_config: Mapping[str, Any] | None = None,
) -> Any:
    """Create an ipywidgets replay control for a portfolio backtest result."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build replay widget: {exc}"

    events = build_portfolio_replay_events(portfolio_result)
    combined = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
    equity_frame = getattr(portfolio_result, "equity_frame", pd.DataFrame())
    times = _replay_times(equity=combined, events=events)
    if len(times) == 0:
        return "No replay timeline available. Run a portfolio backtest first."

    cfg = dict((run_config or {}).get("portfolio_replay", {}) if run_config else {})
    symbols = list(getattr(portfolio_result, "symbol_keys", []) or getattr(portfolio_result, "symbol_results", {}).keys())
    default_symbols = list(cfg.get("selected_symbols", symbols[: min(5, len(symbols))]))
    default_symbols = [sym for sym in default_symbols if sym in symbols] or symbols[: min(5, len(symbols))]

    slider = widgets.IntSlider(
        value=len(times) - 1,
        min=0,
        max=len(times) - 1,
        step=1,
        description="Time:",
        continuous_update=False,
        layout=widgets.Layout(width="560px"),
    )
    play = widgets.Play(
        value=slider.value,
        min=slider.min,
        max=slider.max,
        step=1,
        interval=int(cfg.get("default_speed_ms", 300)),
        description="Play",
    )
    widgets.jslink((play, "value"), (slider, "value"))
    symbol_filter = widgets.SelectMultiple(
        options=symbols,
        value=tuple(default_symbols),
        description="Symbols:",
        rows=min(8, max(3, len(symbols))),
        layout=widgets.Layout(width="260px"),
    )
    output = widgets.Output()

    def render(_: Any = None) -> None:
        output.clear_output(wait=True)
        with output, _widget_output_target(output):
            current_time = times[slider.value]
            render_portfolio_replay_frame(
                portfolio_result,
                current_time,
                events=events,
                selected_symbols=list(symbol_filter.value),
            )

    slider.observe(render, names="value")
    symbol_filter.observe(render, names="value")
    render()
    return widgets.VBox(
        [
            widgets.HTML("<b>Portfolio replay</b>"),
            widgets.HBox([play, slider]),
            symbol_filter,
            output,
        ]
    )


def render_symbol_replay_frame(
    result: Any,
    current_time: Any,
    *,
    events: pd.DataFrame | None = None,
    lookback_bars: int = 300,
    show_signals: bool = True,
    show_entries: bool = True,
    show_exits: bool = True,
) -> dict[str, Any]:
    """Render one replay frame for a symbol and return its state summary."""
    events = build_symbol_replay_events(result) if events is None else events
    equity = getattr(result, "equity", pd.Series(dtype=float))
    state = replay_state_at(events, current_time, equity=equity)
    symbol = str(getattr(result, "symbol", "") or "")
    _display_replay_cards(
        f"{symbol} replay",
        {
            "Time": state["current_time"],
            "Equity": state["equity"],
            "Drawdown %": state["drawdown_pct"],
            "Closed": state["closed_trades"],
            "Open": state["open_trades"],
            "PnL": state["pnl_usd"],
        },
    )
    plot_replay_frame(
        getattr(result, "signal_data", pd.DataFrame()),
        events,
        current_time=current_time,
        equity=equity,
        symbol=symbol,
        lookback_bars=lookback_bars,
        show_signals=show_signals,
        show_entries=show_entries,
        show_exits=show_exits,
    )
    _display_recent_events(events, current_time, symbols=[symbol] if symbol else None)
    return state


def render_portfolio_replay_frame(
    portfolio_result: Any,
    current_time: Any,
    *,
    events: pd.DataFrame | None = None,
    selected_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Render one replay frame for a portfolio and return its state summary."""
    events = build_portfolio_replay_events(portfolio_result) if events is None else events
    selected = selected_symbols or list(getattr(portfolio_result, "symbol_keys", []))
    filtered = events[events["symbol"].isin(selected)].copy() if selected and not events.empty else events
    combined = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
    state = replay_state_at(filtered, current_time, equity=combined)
    _display_replay_cards(
        "Portfolio replay",
        {
            "Time": state["current_time"],
            "Equity": state["equity"],
            "Drawdown %": state["drawdown_pct"],
            "Closed": state["closed_trades"],
            "Open": state["open_trades"],
            "PnL": state["pnl_usd"],
        },
    )
    plot_portfolio_replay_frame(
        portfolio_result,
        current_time=current_time,
        events=filtered,
        selected_symbols=selected,
    )
    _display_recent_events(filtered, current_time, symbols=selected)
    return state


def plot_replay_frame(
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
) -> Any:
    """Plot price markers and optional equity/drawdown up to current_time."""
    import matplotlib.pyplot as plt

    ts = pd.to_datetime(current_time, errors="coerce")
    fig, axes = plt.subplots(2, 1, figsize=(16, 9), sharex=False)
    price_ax, equity_ax = axes

    df = signal_data.copy() if isinstance(signal_data, pd.DataFrame) else pd.DataFrame()
    if not df.empty:
        idx = pd.to_datetime(_time_index(df), errors="coerce")
        df = df.assign(_replay_time=idx).dropna(subset=["_replay_time"])
        # Rolling replay window: candles grow until lookback_bars, then the viewport
        # slides forward so candles do not become tiny as the backtest advances.
        window = df[df["_replay_time"].le(ts)].tail(max(int(lookback_bars), 1))
        if not window.empty:
            price_ax.plot(window["_replay_time"], window["close"], color="#E6EDF3", lw=1.0, label="close")
            if "ma" in window.columns:
                price_ax.plot(window["_replay_time"], window["ma"], color="#FFD93D", lw=1.0, label="ma")
            start_time = window["_replay_time"].iloc[0]
            visible_events = events[
                pd.to_datetime(events["time"], errors="coerce").between(start_time, ts)
            ].copy()
            _scatter_events(price_ax, visible_events, show_signals, show_entries, show_exits)
        else:
            price_ax.set_title("No price data before selected time")
    else:
        price_ax.set_title("No signal_data available")

    price_ax.set_title(f"{symbol} replay price".strip())
    price_ax.grid(alpha=0.25)
    price_ax.legend(loc="best")

    _plot_equity_slice(equity_ax, equity, ts)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


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
    """Render a flicker-resistant candlestick replay frame for widgets.Image."""
    from io import BytesIO
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    ts = pd.to_datetime(current_time, errors="coerce")
    fig = Figure(figsize=(16, 10))
    FigureCanvasAgg(fig)
    axes = fig.subplots(3, 1, gridspec_kw={"height_ratios": [3.2, 1.0, 1.1]}, sharex=False)
    price_ax, macd_ax, equity_ax = axes
    df = _symbol_window(signal_data, ts, lookback_bars)

    if df.empty:
        price_ax.set_title("No OHLC data before selected time")
    else:
        _draw_candlesticks(price_ax, df)
        if "ma" in df.columns:
            price_ax.plot(range(len(df)), df["ma"], color="#F59E0B", lw=1.1, label="MA")
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
        _plot_macd_panel(macd_ax, df)
    if df.empty:
        macd_ax.set_title("No MACD data")

    price_ax.set_title(f"{symbol} candlestick replay".strip())
    price_ax.grid(alpha=0.20)
    handles, labels = price_ax.get_legend_handles_labels()
    if handles:
        price_ax.legend(handles, labels, loc="best")
    _plot_equity_slice(equity_ax, equity, ts)
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    return buf.getvalue()


def _symbol_window(signal_data: pd.DataFrame, ts: pd.Timestamp, lookback_bars: int) -> pd.DataFrame:
    if not isinstance(signal_data, pd.DataFrame) or signal_data.empty:
        return pd.DataFrame()
    required = {"open", "high", "low", "close"}
    if not required.issubset(signal_data.columns):
        return pd.DataFrame()
    idx = pd.to_datetime(_time_index(signal_data), errors="coerce")
    df = signal_data.copy()
    df["_replay_time"] = idx
    df = df.dropna(subset=["_replay_time"]).sort_values("_replay_time")
    return df[df["_replay_time"].le(ts)].tail(max(int(lookback_bars), 1)).reset_index(drop=True)


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


def _plot_macd_panel(ax: Any, frame: pd.DataFrame) -> None:
    if "macd_h" not in frame.columns:
        ax.set_title("MACD histogram unavailable")
        ax.grid(alpha=0.20)
        return
    values = pd.to_numeric(frame["macd_h"], errors="coerce").fillna(0.0)
    colors = ["#22C55E" if value >= 0 else "#EF4444" for value in values]
    ax.bar(range(len(frame)), values, color=colors, alpha=0.75, width=0.72)
    ax.axhline(0, color="#9CA3AF", lw=0.8)
    ax.set_title("MACD histogram")
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
        ({"POSITION_OPENED", "PENDING_FILLED"}, "^", "#22C55E", "entry", show_entries),
        ({"PENDING_CREATED"}, ">", "#60A5FA", "pending", show_entries),
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


def plot_portfolio_replay_frame(
    portfolio_result: Any,
    *,
    current_time: Any,
    events: pd.DataFrame,
    selected_symbols: list[str] | None = None,
) -> Any:
    """Plot portfolio equity, drawdown, and recent event density."""
    import matplotlib.pyplot as plt

    ts = pd.to_datetime(current_time, errors="coerce")
    combined = getattr(portfolio_result, "combined_equity", pd.Series(dtype=float))
    equity_frame = getattr(portfolio_result, "equity_frame", pd.DataFrame())

    fig, axes = plt.subplots(3, 1, figsize=(16, 11), sharex=False)
    _plot_equity_slice(axes[0], combined, ts, title="Combined equity")

    if isinstance(equity_frame, pd.DataFrame) and not equity_frame.empty:
        frame = equity_frame.sort_index().loc[equity_frame.sort_index().index <= ts]
        cols = [c for c in (selected_symbols or list(frame.columns)) if c in frame.columns]
        if cols and not frame.empty:
            frame[cols].tail(500).plot(ax=axes[1], lw=1.1, title="Per-symbol equity")
        else:
            axes[1].set_title("No selected per-symbol equity")
    else:
        axes[1].set_title("No per-symbol equity frame")

    past = events[pd.to_datetime(events["time"], errors="coerce") <= ts].copy()
    if not past.empty:
        event_map = {
            "ENTRY": "ENTRY",
            "POSITION_OPENED": "ENTRY",
            "EXIT": "EXIT",
            "STOP_LOSS_HIT": "EXIT",
            "TAKE_PROFIT_HIT": "EXIT",
            "REVERSAL_CLOSE": "EXIT",
            "FORCE_CLOSE_END_OF_DATA": "EXIT",
        }
        chart_events = past[past["event_type"].isin(event_map)].copy()
        chart_events["event_group"] = chart_events["event_type"].map(event_map)
        counts = chart_events.groupby(["symbol", "event_group"]).size()
        if not counts.empty:
            counts.unstack(fill_value=0).plot(kind="bar", ax=axes[2], title="Entry/exit events so far")
        else:
            axes[2].set_title("No entry/exit events yet")
    else:
        axes[2].set_title("No replay events yet")

    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def _event_frame(rows: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
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
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame = frame.dropna(subset=["time"])
    frame["_event_order"] = frame["event_type"].map(EVENT_ORDER).fillna(99).astype(int)
    frame = frame.sort_values(["time", "_event_order", "symbol"]).reset_index(drop=True)
    return frame.drop(columns=["_event_order"])


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
    value = _series_values_at(series, pd.Series([pd.to_datetime(ts, errors="coerce")])).iloc[0]
    return float(value) if pd.notna(value) else float("nan")


def _series_values_at(series: pd.Series, times: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(np.nan, index=times.index)
    s = series.sort_index()
    idx = pd.to_datetime(s.index, errors="coerce")
    s = pd.Series(s.to_numpy(dtype=float), index=idx).dropna()
    if s.empty:
        return pd.Series(np.nan, index=times.index)
    query = pd.to_datetime(times, errors="coerce")
    positions = s.index.searchsorted(query, side="right") - 1
    values = []
    for pos in positions:
        values.append(float(s.iloc[pos]) if pos >= 0 else np.nan)
    return pd.Series(values, index=times.index)


def _replay_times(
    *,
    signal_data: pd.DataFrame | None = None,
    equity: pd.Series | None = None,
    events: pd.DataFrame | None = None,
) -> pd.DatetimeIndex:
    parts: list[pd.DatetimeIndex] = []
    if isinstance(signal_data, pd.DataFrame) and not signal_data.empty:
        parts.append(pd.DatetimeIndex(pd.to_datetime(_time_index(signal_data), errors="coerce")).dropna())
    if isinstance(equity, pd.Series) and not equity.empty:
        parts.append(pd.DatetimeIndex(pd.to_datetime(equity.index, errors="coerce")).dropna())
    if isinstance(events, pd.DataFrame) and not events.empty and "time" in events.columns:
        parts.append(pd.DatetimeIndex(pd.to_datetime(events["time"], errors="coerce")).dropna())
    if not parts:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted(set().union(*[set(p) for p in parts])))


def _scatter_events(
    ax: Any,
    events: pd.DataFrame,
    show_signals: bool,
    show_entries: bool,
    show_exits: bool,
) -> None:
    if events.empty:
        return
    for event_types, marker, color, label, enabled in [
        ({"SIGNAL", "SIGNAL_DETECTED"}, "o", "#9CA3AF", "signal", show_signals),
        ({"ENTRY", "POSITION_OPENED", "PENDING_FILLED"}, "^", "#22C55E", "entry", show_entries),
        ({"PENDING_CREATED"}, ">", "#60A5FA", "pending", show_entries),
        ({"PARTIAL_TP", "PARTIAL_TP_HIT"}, "D", "#F59E0B", "partial TP", show_exits),
        (
            {"EXIT", "STOP_LOSS_HIT", "TAKE_PROFIT_HIT", "REVERSAL_CLOSE", "FORCE_CLOSE_END_OF_DATA"},
            "x",
            "#EF4444",
            "exit",
            show_exits,
        ),
    ]:
        if not enabled:
            continue
        rows = events[events["event_type"].isin(event_types)]
        rows = rows[pd.to_numeric(rows["price"], errors="coerce").notna()]
        if not rows.empty:
            ax.scatter(rows["time"], rows["price"], marker=marker, s=55, color=color, label=label)


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
    eq = equity.sort_index().loc[equity.sort_index().index <= ts]
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


def _display_replay_cards(title: str, items: Mapping[str, Any]) -> None:
    _display_html(_replay_cards_html(title, items))


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


def _display_recent_events(
    events: pd.DataFrame,
    current_time: Any,
    *,
    symbols: list[str] | None = None,
    tail: int = 25,
) -> pd.DataFrame:
    if events.empty:
        print("No replay events.")
        return pd.DataFrame()
    ts = pd.to_datetime(current_time, errors="coerce")
    view = events[pd.to_datetime(events["time"], errors="coerce") <= ts].copy()
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
    for col in ["price", "sl", "tp", "lot_size", "pnl_usd", "equity"]:
        if col in view.columns:
            view[col] = pd.to_numeric(view[col], errors="coerce")
    _display_html(
        "<div style='font-size:16px;font-weight:700;margin-top:10px;color:#111827;'>Recent replay events</div>"
    )
    if view.empty:
        print("No events before selected time.")
        return view
    _display_obj(view.style.format(_table_formatters(view)).hide(axis="index"))
    return view


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
    view = events[pd.to_datetime(events["time"], errors="coerce") <= ts].copy()
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
        body = display_view.to_html(index=False, border=0, classes="combo-replay-table")
    return f"""
    <div style="margin-top:10px;">
      <div style="font-size:16px;font-weight:700;color:#111827;margin-bottom:6px;">Recent replay events</div>
      <style>
        .combo-replay-table {{
          border-collapse: collapse;
          width: 100%;
          font-size: 12px;
          color: #111827;
        }}
        .combo-replay-table th {{
          background: #E5E7EB;
          padding: 6px 8px;
          text-align: left;
          border-bottom: 1px solid #D1D5DB;
        }}
        .combo-replay-table td {{
          padding: 5px 8px;
          border-bottom: 1px solid #F1F5F9;
        }}
      </style>
      <div style="overflow-x:auto;">{body}</div>
    </div>
    """


__all__ = [
    "build_symbol_replay_events",
    "build_portfolio_replay_events",
    "replay_state_at",
    "build_symbol_replay_widget",
    "build_symbol_replay_backtest_widget",
    "build_portfolio_replay_widget",
    "render_symbol_replay_frame",
    "render_portfolio_replay_frame",
    "plot_replay_frame",
    "plot_portfolio_replay_frame",
]
