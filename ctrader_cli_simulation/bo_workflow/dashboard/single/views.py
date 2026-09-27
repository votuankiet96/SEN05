"""Pure Dash presentation for the Single Backtest Session feature.

No function here writes state or imports core_engine. Callback orchestration is
kept in app.py; session persistence is session_store.py; core artifact reads
are data_access.py.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import dash_table, dcc, html
import dash_mantine_components as dmc
try:
    from .. import signal_sources
except ImportError:  # app.py loads feature folders as top-level modules.
    import signal_sources


def layout(options: dict[str, Any]) -> html.Div:
    strategies = options["strategies"]
    combo = next(item for item in strategies if item["value"] == "combo")
    symbols = options["symbols"]
    default_symbol = next(
        (item["value"] for item in symbols if item["value"] == "US30.cash"),
        symbols[0]["value"],
    )
    today = date.today().isoformat()
    return html.Div([
        # This polls only the four small run-status controls.  It never
        # replaces the saved-session selector, tables, charts, or detail view.
        dcc.Interval(
            id="session-progress-poll", interval=1_000, n_intervals=0, disabled=True,
        ),
        dcc.Store(id="session-selection-request", data=None),
        dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
            dmc.Group([
                dmc.Stack([
                    dmc.Title("Single Backtest Sessions", order=3),
                    dmc.Text(
                        "Each session runs exactly one cTrader CLI process. Creating a session freezes its inputs; "
                        "running it is a separate action.",
                        c="dimmed",
                    ),
                ], gap="xs"),
                dmc.Badge("Dashboard guard: 1 run", color="orange", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Divider(),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.Select(
                    id="session-strategy-select", label="Strategy",
                    data=[{"label": item["label"], "value": item["value"]} for item in strategies],
                    value="combo", clearable=False,
                ),
                dmc.MultiSelect(
                    id="session-symbol-select", label="Symbols",
                    data=symbols, value=[default_symbol],
                    clearable=True, searchable=True,
                ),
                dmc.MultiSelect(
                    id="session-timeframe-select", label="Timeframes",
                    data=[{"label": tf.upper(), "value": tf} for tf in combo["timeframes"]],
                    value=[combo["timeframes"][0]], clearable=True,
                ),
            ]),
            dmc.Text(
                "One draft is created for every selected Symbol × Timeframe combination. "
                "Creating drafts never starts cTrader CLI.",
                size="xs", c="dimmed",
            ),
            dmc.Divider(label="Backtest inputs"),
            dmc.SimpleGrid(cols=4, spacing="md", children=[
                dmc.NumberInput(
                    id="session-balance-input", label="Starting balance (USD)",
                    value=100000, min=1, step=1000, suffix=" USD",
                    thousandSeparator=",",
                ),
                dmc.NumberInput(
                    id="session-risk-input", label="Risk per trade", value=0.5,
                    min=0.01, step=0.01, suffix=" %",
                ),
                dmc.Select(
                    id="session-ksl-input", label="kSL Fibonacci level",
                    data=combo["ksl_options"], value=combo["default_ksl"], clearable=False,
                ),
                dmc.Select(
                    id="session-ktp-input", label="kTP Fibonacci level",
                    data=combo["ktp_options"], value=combo["default_ktp"], clearable=False,
                ),
            ]),
            html.Div(id="session-history-status"),
            dmc.SimpleGrid(cols=2, spacing="md", children=[
                dmc.DateInput(
                    id="session-start-input", label="Start date", value="2025-01-01",
                    valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=today,
                    clearable=False, inputProps={"readOnly": True},
                ),
                dmc.DateInput(
                    id="session-end-input", label="End date", value=today,
                    valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=today,
                    clearable=False, inputProps={"readOnly": True},
                ),
            ]),
            dmc.Group([
                dmc.Button("Create session (does not run)", id="create-session-btn", color="blue"),
                html.Div(id="session-create-message"),
            ], align="center"),
        ])),
        dmc.Paper(withBorder=True, radius="md", p="md", mt="md", children=dmc.Stack([
            dmc.Group([
                dmc.Title("Saved sessions", order=4),
                dmc.Button(
                    "Refresh sessions", id="refresh-session-view-btn",
                    variant="light", size="xs",
                ),
            ], justify="space-between", align="center"),
            dmc.MultiSelect(
                id="session-select", label="Select saved sessions", data=[],
                placeholder="No sessions yet", clearable=True, searchable=True,
            ),
            dmc.Text(
                "Select one completed or cached session to inspect it. Select one or more drafts to queue them "
                "in order; only one cTrader CLI process can run at a time.",
                size="xs", c="dimmed",
            ),
            dmc.Group([
                dmc.Button("Run selected drafts", id="run-session-btn", color="orange"),
                dmc.Button("Stop active run", id="stop-session-btn", color="red", disabled=True),
                dmc.Button(
                    "Delete selected sessions", id="delete-selected-session-btn",
                    color="red", variant="outline", disabled=True,
                ),
            ], align="end"),
            dmc.Progress(id="session-progress-bar", value=0, striped=True, animated=True),
            dmc.Text(id="session-progress-label", children="No active run", size="sm", c="dimmed"),
            html.Div(id="session-run-message"),
            html.Div(id="session-stop-message"),
            dmc.Text(
                "Delete removes dashboard metadata only; core reports and pipeline caches are retained.",
                size="xs", c="dimmed",
            ),
            html.Div(id="session-delete-message"),
        ])),
        html.Div(id="session-detail", style={"marginTop": "1rem"}),
    ])


def select_data(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "value": item["session_id"],
            "label": f"{item['title']} — {signal_sources.label_from_input((item.get('plan') or {}).get('input'))} — {item['state']} — {item['created_utc'][:19]}",
        }
        for item in sessions
    ]


def selection_detail(sessions: list[dict[str, Any]]) -> Any:
    """Present a multi-draft queue without reading or changing core artifacts."""
    if not sessions:
        return detail(None, None, [])
    non_drafts = [session for session in sessions if session.get("state") != "draft"]
    if non_drafts:
        return dmc.Alert(
            "Multiple selection is available for draft sessions only. Select one completed or cached session to inspect its result.",
            title="Selection cannot be queued", color="orange", variant="light",
        )
    rows = []
    for index, session in enumerate(sessions, start=1):
        input_doc = session.get("plan", {}).get("input", {})
        rows.append(dmc.Group([
            dmc.Badge(str(index), variant="light", color="blue", circle=True),
            dmc.Text(session.get("title") or "Untitled session", size="sm", style={"flex": 1}),
            dmc.Badge(str(session.get("state") or "unknown").upper(), variant="light", color="gray"),
            dmc.Text(
                f"{input_doc.get('symbol', '—')} · {str(input_doc.get('timeframe', '—')).upper()}",
                size="xs", c="dimmed",
            ),
        ], wrap="nowrap", align="center"))
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
        dmc.Title(f"{len(sessions)} draft sessions selected", order=4),
        dmc.Text(
            "They will run in the displayed order when Run selected drafts is pressed. "
            "The queue stops if a run is cancelled or fails; remaining drafts stay untouched.",
            size="sm", c="dimmed",
        ),
        dmc.Divider(),
        dmc.Stack(rows, gap="xs"),
    ], gap="sm"))


def detail(
    session: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    events: list[dict[str, Any]],
    form_options: dict[str, Any] | None = None,
) -> Any:
    if not session:
        return dmc.Paper(withBorder=True, radius="md", p="xl", children=dmc.Text(
            "Create a session or select an existing one to view its inputs and results.", c="dimmed"
        ))
    execution = execution or {}
    state = session["state"]
    state_color = {
        "draft": "gray", "running": "yellow", "completed": "green",
        "cached": "teal", "cancelled": "orange", "failed": "red",
    }.get(state, "gray")
    plan = session["plan"]
    input_doc = plan.get("input") or {}
    preflight = plan.get("preflight") or {}
    header = dmc.Group([
        dmc.Stack([
            dmc.Title(session["title"], order=3),
            dmc.Text(f"Session ID: {session['session_id']}", size="sm", c="dimmed"),
        ], gap="xs"),
        dmc.Badge(state.upper(), color=state_color, size="lg"),
    ], justify="space-between", align="flex-start")
    progress = float(session.get("progress_pct") or 0)
    progress_text = str(session.get("progress_text") or state.replace("_", " ").title())
    sections: list[Any] = [
        header,
        dmc.Progress(
            value=progress, color=state_color, striped=state == "running", animated=state == "running",
        ),
        dmc.Text(format_progress(progress, progress_text), size="sm", c="dimmed"),
    ]
    if session.get("note"):
        sections.append(dmc.Text(session["note"], fs="italic"))
    if session.get("error_text"):
        sections.append(dmc.Alert(session["error_text"], title="Session error", color="red"))

    sections.extend([
        dmc.Divider(label="Backtest configuration"),
        _input_summary(input_doc, preflight, form_options),
    ])
    if execution.get("read_error"):
        sections.append(dmc.Alert(execution["read_error"], title="Could not read artifact", color="red"))
    elif execution.get("row"):
        sections.extend(_execution_sections(execution))
    elif state == "running":
        sections.append(dmc.Alert(
            "The core engine is running. Use Refresh sessions when you want to read the latest progress; "
            "successful-run logs appear after the core engine finishes writing artifacts.",
            title="Running", color="yellow", variant="light",
        ))
    else:
        sections.append(dmc.Text("No execution artifact exists for this session yet.", c="dimmed"))

    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))


def _execution_sections(execution: dict[str, Any]) -> list[Any]:
    row = execution["row"]
    metrics = execution.get("metrics") or {}
    status_color = "green" if row.get("status") == "ok" else "red"
    result: list[Any] = [
        dmc.Divider(label="Execution"),
        dmc.Group([
            dmc.Badge(f"core status: {row.get('status')}", color=status_color),
            dmc.Text(f"Run ID: {row.get('run_id')}"),
            dmc.Text(f"Wall: {row.get('wall_seconds')} s"),
        ]),
        _metric_grid(metrics, execution.get("execution_summary") or {}),
    ]
    flags = row.get("validity_flags") or {}
    if flags:
        result.append(_validity_summary(flags))
    points = execution.get("equity_points") or []
    if points:
        result.append(_equity_panel(points))
    history = execution.get("history") or []
    if history:
        result.append(_history_table(history))
    bot_events = execution.get("bot_events") or []
    cli_log = execution.get("cli_log") or ""
    if bot_events:
        result.append(_bot_activity_table(bot_events))
    else:
        result.append(dmc.Text("No recognised cBot activity is available in the persisted run log.", c="dimmed", size="sm"))
    if cli_log:
        result.extend([dmc.Divider(label="CLI log"), _log_panel("CLI output", cli_log)])
    elif row.get("status") == "ok":
        result.append(dmc.Text("No CLI log was retained for this successful run.", c="dimmed", size="xs"))
    return result


def _metric_grid(metrics: dict[str, Any], execution_summary: dict[str, Any]) -> Any:
    profit = metrics.get("net_profit")
    profit_colour = "#16a34a" if isinstance(profit, (int, float)) and profit >= 0 else "#dc2626"
    total_trades = metrics.get("total_trades")

    def card(label: str, value: str, note: str = "", accent: str | None = None) -> Any:
        style = {"borderTop": f"3px solid {accent}"} if accent else None
        return dmc.Paper(withBorder=True, radius="sm", p="sm", style=style, children=dmc.Stack([
            dmc.Text(label, size="xs", c="dimmed", fw=500),
            dmc.Text(value, fw=700, size="lg" if accent else "md"),
            dmc.Text(note, size="xs", c="dimmed") if note else html.Span(),
        ], gap=2))

    headline = [
        card("Net profit", _format_value(profit, "money"), accent=profit_colour),
        card("Return on capital", _format_value(metrics.get("roi"), "percent"), accent="#2563eb"),
        card("Profit factor", _format_value(metrics.get("profit_factor"), "number"), accent="#7c3aed"),
        card("Closing balance", _format_value(metrics.get("ending_balance"), "money"), accent="#0891b2"),
    ]
    trade_outcomes = [
        card("Winning trades", _fraction(metrics.get("winning_trades"), total_trades), "winning / total"),
        card("Losing trades", _fraction(metrics.get("losing_trades"), total_trades), "losing / total"),
        card("Largest winning trade", _format_value(metrics.get("largest_win"), "money")),
        card("Largest losing trade", _format_value(metrics.get("largest_loss"), "money")),
    ]
    risk_and_costs = [
        card(
            "Max equity drawdown",
            _drawdown_value(metrics.get("max_equity_drawdown_pct"), metrics.get("max_equity_drawdown_abs")),
            "cTrader report",
        ),
        card(
            "Max balance drawdown",
            _drawdown_value(metrics.get("max_balance_drawdown_pct"), metrics.get("max_balance_drawdown_abs")),
            "cTrader report",
        ),
        card("Swap", _format_value(metrics.get("swaps"), "money")),
        card("Commission", _format_value(metrics.get("commissions"), "money")),
    ]
    funnel = [
        card(
            "Signals in test period",
            _fraction(execution_summary.get("processed"), execution_summary.get("loaded")),
            "processed / rows loaded by cBot",
        ),
        card(
            "Pending orders placed",
            _fraction(execution_summary.get("placed"), execution_summary.get("processed")),
            "placed / processed signals",
        ),
        card(
            "Positions filled",
            _fraction(execution_summary.get("filled"), execution_summary.get("placed")),
            "filled / pending orders",
        ),
        card(
            "Take-profit exits",
            _fraction(execution_summary.get("take_profit_exits"), total_trades),
            "cBot-confirmed TP / total trades",
        ),
        card(
            "Stop-loss exits",
            _fraction(execution_summary.get("stop_loss_exits"), total_trades),
            "cBot-confirmed SL / total trades",
        ),
        card(
            "Other exits",
            _fraction(execution_summary.get("other_exits"), total_trades),
            "reversal or other closes",
        ),
        card(
            "Exit reason unavailable",
            _fraction(execution_summary.get("unattributed_exits"), total_trades),
            "history trade without a close event in cBot log",
        ),
        card("Pending orders expired", _format_value(execution_summary.get("pending_expired"), "integer")),
        card("Same-direction signals skipped", _format_value(execution_summary.get("same_direction_skipped"), "integer")),
    ]

    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Text("Results overview", fw=600, size="sm"),
            dmc.Text("cTrader report + cBot run evidence", size="xs", c="dimmed"),
        ], justify="space-between"),
        dmc.SimpleGrid(cols=4, spacing="xs", children=headline),
        dmc.Divider(label="Trade outcomes"),
        dmc.SimpleGrid(cols=4, spacing="xs", children=trade_outcomes),
        dmc.Divider(label="Risk and costs"),
        dmc.SimpleGrid(cols=4, spacing="xs", children=risk_and_costs),
        dmc.Divider(label="Signal-to-execution funnel"),
        dmc.SimpleGrid(cols=3, spacing="xs", children=funnel),
    ], gap="xs"))


def _fraction(numerator: Any, denominator: Any) -> str:
    if not isinstance(numerator, int) or not isinstance(denominator, int) or denominator <= 0:
        return "—"
    percentage = numerator / denominator * 100
    return f"{numerator:,} / {denominator:,} · {_format_value(percentage, 'percent')}"


def _drawdown_value(percent: Any, absolute: Any) -> str:
    if percent in (None, ""):
        return "—"
    absolute_text = _format_value(absolute, "money")
    return f"{_format_value(percent, 'percent')} · {absolute_text}"


def _input_summary(
    input_doc: dict[str, Any],
    preflight: dict[str, Any],
    form_options: dict[str, Any] | None = None,
) -> Any:
    fixed = input_doc.get("fixed_params") or {}
    strategy = str(input_doc.get("strategy") or "")
    strategy_label = "MA Cross" if strategy == "macross" else "Combo" if strategy == "combo" else strategy
    cards = (
        ("Strategy", strategy_label),
        ("Symbol", input_doc.get("symbol")),
        ("Timeframe", str(input_doc.get("timeframe", "—")).upper()),
        ("Period", f"{input_doc.get('start', '—')} → {input_doc.get('end', '—')}"),
        ("Starting balance", _format_value(input_doc.get("balance"), "money")),
        ("Risk per trade", _format_value(fixed.get("RiskPercent"), "percent")),
        (
            "kSL Fibonacci level",
            _fib_display(fixed.get("KslLevel"), strategy, "ksl_options", form_options),
        ),
        (
            "kTP Fibonacci level",
            _fib_display(fixed.get("KtpLevel"), strategy, "ktp_options", form_options),
        ),
    )
    summary = dmc.SimpleGrid(cols=4, spacing="xs", children=[
        dmc.Paper(withBorder=True, radius="sm", p="sm", children=[
            dmc.Text(label, size="xs", c="dimmed"),
            dmc.Text(str(value if value not in (None, "") else "—"), fw=600),
        ])
        for label, value in cards
    ])
    algorithm = Path(str(preflight.get("algo_path") or "")).name or "—"
    runtime_cards = (
        ("Data mode", str(preflight.get("data_mode") or "—").upper()),
        ("Signal source", signal_sources.label_from_input(input_doc)),
        ("Algorithm", algorithm),
    )
    return dmc.Stack([
        summary,
        dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
            dmc.Text("Runtime context", fw=500, size="sm"),
            dmc.SimpleGrid(cols=3, spacing="xs", children=[
                dmc.Stack([
                    dmc.Text(label, size="xs", c="dimmed"),
                    dmc.Text(str(value), size="sm"),
                ], gap=2)
                for label, value in runtime_cards
            ]),
        ], gap="xs")),
    ], gap="xs")


def _validity_summary(flags: dict[str, Any]) -> Any:
    fields = (
        ("Period matches request", flags.get("period_ok")),
        ("Signal loaded", flags.get("signal_ok")),
        ("Margin rejections", flags.get("margin_rejections")),
        ("Approximate FX", flags.get("approx_fx")),
    )

    def display(value: Any) -> str:
        if value is True:
            return "Yes"
        if value is False:
            return "No"
        if value in (None, ""):
            return "Unknown"
        return str(value)

    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Text("Validation", fw=500, size="sm"),
        dmc.SimpleGrid(cols=4, spacing="xs", children=[
            dmc.Stack([
                dmc.Text(label, size="xs", c="dimmed"),
                dmc.Text(display(value), size="sm"),
            ], gap=2)
            for label, value in fields
        ]),
    ], gap="xs"))


def format_progress(percent: float | int | None, detail: str = "") -> str:
    value = _format_value(percent, "number")
    return f"{value}% — {detail}" if detail else f"{value}%"


def _format_value(value: Any, kind: str) -> str:
    if value in (None, ""):
        return "—"
    if kind == "integer":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "—"
        if number == number.to_integral_value():
            text = f"{int(number):,}"
        else:
            text = f"{number.quantize(Decimal('0.01'), rounding=ROUND_DOWN):,}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if kind == "money":
        return f"{text} USD"
    if kind == "percent":
        return f"{text}%"
    return text


def _fib_display(
    value: Any,
    strategy: str = "",
    option_key: str = "",
    form_options: dict[str, Any] | None = None,
) -> str:
    raw = str(value or "")
    for strategy_option in (form_options or {}).get("strategies", []):
        if strategy_option.get("value") != strategy:
            continue
        for option in strategy_option.get(option_key, []):
            if str(option.get("value")) == raw:
                return str(option.get("label") or raw)
    digits = raw.removeprefix("Fib")
    if len(digits) == 4 and digits.isdigit():
        return f"Fib {digits[0]}.{digits[1:]}"
    return raw or "—"


def _equity_panel(points: list[dict[str, Any]]) -> Any:
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Stack([
                dmc.Text("Account value and equity drawdown", fw=500, size="sm"),
                dmc.Text(
                    "Shared time axis: account value above; equity drawdown below its zero line.",
                    size="xs", c="dimmed",
                ),
            ], gap=2),
            dmc.Badge("Hover to inspect", variant="light", color="red"),
        ], justify="space-between", align="flex-start"),
        dcc.Graph(
            id="equity-dd-chart",
            figure=_equity_figure(points),
            # This is an inspection chart, not an editing surface.  Keep its
            # time range stable so a hover cannot be mistaken for a zoom/pan.
            config={
                "displaylogo": False,
                "displayModeBar": False,
                "responsive": True,
                "scrollZoom": False,
                "doubleClick": False,
            },
            style={"height": "32rem"},
        ),
    ], gap="xs"))


def _equity_figure(points: list[dict[str, Any]]) -> go.Figure:
    x = [_chart_time(item.get("timestamp") or item.get("time") or item.get("date")) for item in points]
    balance = [item.get("balance") for item in points]
    min_equity = [item.get("minEquity") for item in points]
    max_equity = [item.get("maxEquity") for item in points]
    drawdown = _drawdown_series(balance, min_equity, max_equity)
    value_hover = [
        [_format_value(value, "money"), _format_value(drawdown_value, "percent")]
        for value, drawdown_value in zip(balance, drawdown)
    ]
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.60, 0.40], vertical_spacing=0.07,
    )
    figure.add_trace(go.Scatter(
        x=x, y=balance, mode="lines", name="Account value",
        line={"width": 2.5, "color": "#2563eb", "shape": "hv"},
        fill="tozeroy", fillcolor="rgba(37, 99, 235, 0.08)",
        customdata=value_hover,
        hovertemplate=(
            "<b>Account value</b>: %{customdata[0]}<br>"
            "<b>Equity drawdown</b>: %{customdata[1]}<extra></extra>"
        ),
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=x, y=drawdown, mode="lines", name="Equity drawdown",
        line={"width": 1.5, "color": "#dc2626"},
        fill="tozeroy", fillcolor="rgba(220, 38, 38, 0.18)",
        customdata=value_hover,
        hovertemplate=(
            "<b>Account value</b>: %{customdata[0]}<br>"
            "<b>Equity drawdown</b>: %{customdata[1]}<extra></extra>"
        ),
    ), row=2, col=1)
    account_domain = figure.layout.yaxis.domain
    drawdown_domain = figure.layout.yaxis2.domain
    figure.update_layout(
        template="plotly_white",
        # Axis titles are fixed annotations below.  A pixel-based xshift keeps
        # them on exactly the same vertical line even though USD tick labels
        # are much wider than percentage tick labels.
        margin={"t": 12, "r": 18, "b": 42, "l": 156},
        hovermode="x unified",
        hoversubplots="axis",
        hoverdistance=-1,
        spikedistance=-1,
        hoverlabel={"bgcolor": "white", "font": {"color": "#1f2937"}},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[
            {
                "text": "Account value (USD)",
                "textangle": -90,
                "xref": "paper", "yref": "paper",
                "x": 0, "xshift": -140,
                "y": sum(account_domain) / 2,
                "xanchor": "center", "yanchor": "middle",
                "showarrow": False,
                "font": {"size": 14, "color": "#1f2937"},
            },
            {
                "text": "Equity drawdown (%)",
                "textangle": -90,
                "xref": "paper", "yref": "paper",
                "x": 0, "xshift": -140,
                "y": sum(drawdown_domain) / 2,
                "xanchor": "center", "yanchor": "middle",
                "showarrow": False,
                "font": {"size": 14, "color": "#1f2937"},
            },
        ],
        xaxis={
            "showgrid": False, "linecolor": "#e5e7eb", "showticklabels": False,
            "showspikes": False, "automargin": False, "fixedrange": True,
        },
        xaxis2={
            "title": None, "showgrid": False, "linecolor": "#e5e7eb",
            "showspikes": False, "automargin": False,
            "ticklabelposition": "outside bottom", "ticklabelstandoff": 12,
            "fixedrange": True,
        },
        yaxis={
            "title": None, "gridcolor": "#e5e7eb", "zeroline": False,
            "automargin": False, "ticklabelstandoff": 8, "fixedrange": True,
            **_equity_axis_ticks(balance),
        },
        yaxis2={
            "title": None, "showgrid": False,
            "zeroline": True, "zerolinecolor": "#fecaca",
            "automargin": False, "ticklabelstandoff": 8, "fixedrange": True,
            **_drawdown_axis(drawdown),
        },
    )
    return figure


def _drawdown_series(
    balance: list[Any], min_equity: list[Any], max_equity: list[Any],
) -> list[float]:
    """Compute current equity drawdown from report-provided intrabar highs/lows."""
    peak: float | None = None
    result: list[float] = []
    for account_value, low, high in zip(balance, min_equity, max_equity):
        high_value = _finite_float(high, fallback=account_value)
        low_value = _finite_float(low, fallback=account_value)
        if high_value is not None:
            peak = max(peak, high_value) if peak is not None else high_value
        if peak is None or low_value is None or peak <= 0:
            result.append(0.0)
        else:
            result.append(min(0.0, (low_value - peak) / peak * 100))
    return result


def _finite_float(value: Any, *, fallback: Any = None) -> float | None:
    candidate = value if value is not None else fallback
    try:
        numeric = float(candidate)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric and abs(numeric) != float("inf") else None


def _chart_time(value: Any) -> Any:
    """Return Plotly a real UTC datetime when artifact timestamps are epoch milliseconds."""
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return value


def _equity_axis_ticks(*series: list[Any]) -> dict[str, Any]:
    """Use human-readable USD tick labels without rounding artifact values."""
    values: list[float] = []
    for values_in_series in series:
        for value in values_in_series:
            if isinstance(value, (int, float)):
                values.append(float(value))
    if not values:
        return {}
    lower, upper = min(values), max(values)
    span = upper - lower
    padding = span * 0.05 if span else max(abs(upper) * 0.05, 1)
    lower -= padding
    upper += padding
    tickvals = [lower + (upper - lower) * index / 4 for index in range(5)]
    return {
        "range": [lower, upper],
        "tickmode": "array",
        "tickvals": tickvals,
        "ticktext": [_format_value(value, "money") for value in tickvals],
    }


def _drawdown_axis(values: list[float]) -> dict[str, Any]:
    lowest = min(values, default=0.0)
    lower = lowest * 1.12 if lowest < 0 else -1.0
    tickvals = [lower + (0 - lower) * index / 4 for index in range(5)]
    return {
        "range": [lower, 0],
        "tickmode": "array",
        "tickvals": tickvals,
        "ticktext": [_format_value(value, "percent") for value in tickvals],
    }


def _history_table(history: list[dict[str, Any]]) -> Any:
    raw_rows = [dict(item) for item in history[:500] if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        net = item.get("net")
        outcome = "profit" if isinstance(net, (int, float)) and net >= 0 else "loss"
        rows.append({
            "id": item.get("id"),
            "symbol": item.get("symbol"),
            "direction": str(item.get("direction") or "—").upper(),
            "entry_time": _trade_time(item.get("entryTime")),
            "entry_price": _format_value(item.get("entryPrice"), "number"),
            "close_time": _trade_time(item.get("closeTime")),
            "close_price": _format_value(item.get("closePrice"), "number"),
            "net": _format_value(net, "money"),
            "gross": _format_value(item.get("gross"), "money"),
            "commissions": _format_value(item.get("commissions"), "money"),
            "swaps": _format_value(item.get("swaps"), "money"),
            "pips": _format_value(item.get("pips"), "number"),
            "volume": _format_value(item.get("volume"), "number"),
            "quantity": _format_value(item.get("quantity"), "number"),
            "balance": _format_value(item.get("balance"), "money"),
            "label": item.get("label") or "—",
            "comment": item.get("comment") or "—",
            "_outcome": outcome,
        })
    columns = [
        ("id", "Trade #"), ("symbol", "Symbol"), ("direction", "Side"),
        ("entry_time", "Entry time"), ("entry_price", "Entry price"),
        ("close_time", "Close time"), ("close_price", "Close price"),
        ("net", "Net P&L"), ("gross", "Gross P&L"),
        ("commissions", "Commission"), ("swaps", "Swap"),
        ("pips", "Pips"), ("volume", "Volume"), ("quantity", "Quantity"),
        ("balance", "Balance"), ("label", "Label"), ("comment", "Comment"),
    ]
    table = dash_table.DataTable(
        columns=[{"name": label, "id": key} for key, label in columns],
        data=rows,
        hidden_columns=["_outcome"],
        page_action="native",
        page_size=12,
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#f8fafc", "color": "#475569", "fontWeight": 600,
            "border": "none", "borderBottom": "1px solid #e2e8f0", "padding": "10px 8px",
        },
        style_cell={
            "backgroundColor": "white", "border": "none", "borderBottom": "1px solid #f1f5f9",
            "color": "#334155", "fontSize": "0.82rem", "padding": "9px 8px",
            "textAlign": "left", "whiteSpace": "nowrap",
        },
        style_data_conditional=[
            {"if": {"filter_query": "{_outcome} = profit", "column_id": "net"}, "color": "#15803d", "fontWeight": 600},
            {"if": {"filter_query": "{_outcome} = loss", "column_id": "net"}, "color": "#dc2626", "fontWeight": 600},
            {"if": {"column_id": "id"}, "color": "#64748b"},
        ],
    )
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Text("Trade history", fw=600, size="sm"),
            dmc.Badge(f"{len(rows):,} closed trades", variant="light", color="blue"),
        ], justify="space-between"),
        table,
    ], gap="xs"))


def _bot_activity_table(events: list[dict[str, Any]]) -> Any:
    rows = [
        {
            "time": _bot_event_time(item.get("time")),
            "side": item.get("side") or "-",
            "reference": item.get("reference") or "-",
            "price": _format_log_context(item.get("price_context")),
            "protection": _format_log_context(item.get("protection")),
            "account": _format_log_context(item.get("account_context")),
            "level": item.get("level") or "—",
            "event": item.get("event") or "—",
            "detail": item.get("detail") or "—",
            "net": _format_value(item.get("net"), "money") if item.get("net") not in (None, "") else "—",
            "_tone": item.get("tone") or "neutral",
        }
        for item in events
        if isinstance(item, dict)
    ]
    table = dash_table.DataTable(
        columns=[
            {"name": "Event time", "id": "time"},
            {"name": "Level", "id": "level"},
            {"name": "Message", "id": "detail"},
        ],
        data=rows,
        hidden_columns=["_tone"],
        page_action="native",
        page_size=20,
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#f8fafc", "color": "#475569", "fontWeight": 600,
            "border": "none", "borderBottom": "1px solid #e2e8f0", "padding": "10px 8px",
        },
        style_cell={
            "backgroundColor": "white", "border": "none", "borderBottom": "1px solid #f1f5f9",
            "color": "#334155", "fontSize": "0.82rem", "padding": "9px 8px",
            "textAlign": "left", "whiteSpace": "nowrap",
        },
        style_cell_conditional=[
            {"if": {"column_id": "time"}, "minWidth": 170, "width": 170, "maxWidth": 170},
            {"if": {"column_id": "level"}, "minWidth": 70, "width": 70, "maxWidth": 90},
            {"if": {"column_id": "detail"}, "minWidth": 760, "width": 960, "maxWidth": 1400, "whiteSpace": "normal", "lineHeight": "1.45"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{_tone} = profit", "column_id": "detail"}, "color": "#15803d"},
            {"if": {"filter_query": "{_tone} = loss", "column_id": "detail"}, "color": "#dc2626"},
            {"if": {"column_id": "time"}, "color": "#64748b"},
        ],
    )
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Stack([
                dmc.Text("cBot activity", fw=600, size="sm"),
                dmc.Text("Complete persisted cBot log, formatted without changing its columns or content", size="xs", c="dimmed"),
            ], gap=2),
            dmc.Badge(f"{len(events):,} log lines", variant="light", color="blue"),
        ], justify="space-between", align="flex-start"),
        table,
    ], gap="xs"))


def _bot_event_time(value: Any) -> str:
    raw = str(value or "").strip()
    for pattern in ("%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).strftime("%d %b %Y, %H:%M:%S")
        except ValueError:
            continue
    return raw or "—"


def _format_log_context(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    parts: list[str] = []
    for part in raw.split(" · "):
        label, separator, numeric = part.partition(" ")
        if not separator:
            parts.append(part)
            continue
        if label in {"Balance", "Budget"}:
            formatted = _format_value(numeric.removeprefix("$"), "money")
        elif label == "Risk":
            formatted = _format_value(numeric.removesuffix("%"), "percent")
        else:
            formatted = _format_value(numeric, "number")
        parts.append(f"{label} {formatted}")
    return " · ".join(parts)


def _trade_time(value: Any) -> str:
    chart_time = _chart_time(value)
    if isinstance(chart_time, datetime):
        return chart_time.strftime("%d %b %Y, %H:%M")
    return str(chart_time or "—")


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _log_panel(title: str, text: str) -> Any:
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=[
        dmc.Text(title, fw=500, size="sm"),
        html.Pre(text, style={
            "maxHeight": "20rem", "overflow": "auto", "whiteSpace": "pre-wrap",
            "fontSize": "0.78rem", "margin": "0.5rem 0 0",
        }),
    ])
