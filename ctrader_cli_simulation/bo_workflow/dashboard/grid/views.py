"""Pure Dash presentation for the Grid Search Session feature.

This module owns no persistence and never imports core_engine.  All component
IDs use the ``grid-`` prefix so the feature remains isolated from Single
Backtest Sessions.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.colors import diverging
from dash import dash_table, dcc, html
import dash_mantine_components as dmc
try:
    from .. import signal_sources
    from ..ui_common import (
        DECIMAL, INPUT_DEBOUNCE_MS, INTEGER, MONEY, PERCENT, PERSIST, SECONDS,
        numeric_column, right_align, selected_dsr, status_region, truncated,
    )
except ImportError:  # app.py loads feature folders as top-level modules.
    import signal_sources
    from ui_common import (
        DECIMAL, INPUT_DEBOUNCE_MS, INTEGER, MONEY, PERCENT, PERSIST, SECONDS,
        numeric_column, right_align, selected_dsr, status_region, truncated,
    )

# Net profit has a meaningful zero, so it needs a diverging scale centred on 0
# (Plotly `zmid`) that stays readable with red-green colour-vision deficiency
# (ColorBrewer RdBu; Crameri et al. 2020). Red = loss, blue = profit.
PROFIT_COLORSCALE = diverging.RdBu


def layout(options: dict[str, Any]) -> html.Div:
    strategies = options["strategies"]
    combo = next(item for item in strategies if item["value"] == "combo")
    symbols = options["symbols"]
    default_symbol = next(
        (item["value"] for item in symbols if item["value"] == "US30.cash"),
        symbols[0]["value"],
    )
    all_ksl = [str(item["value"]) for item in combo["ksl_options"]]
    all_ktp = [str(item["value"]) for item in combo["ktp_options"]]
    today = date.today().isoformat()
    return html.Div([
        dcc.Interval(
            id="grid-progress-poll", interval=1_000, n_intervals=0, disabled=True,
        ),
        dcc.Store(id="grid-selection-request", data=None),
        # Set by the background callback immediately when a Run request starts.
        # This lets the live-status poll begin before the worker has completed
        # its preflight and claimed the persistent session.
        dcc.Store(id="grid-run-state", data={"active": False}),
        dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
            dmc.Group([
                dmc.Stack([
                    dmc.Title("Grid Search Sessions", order=3),
                    dmc.Text(
                        "Run the selected kSL x kTP combinations for one symbol and timeframe. "
                        "Creating a draft never starts cTrader CLI.",
                        c="dimmed",
                    ),
                ], gap="xs"),
                dmc.Badge("Research stage 1", color="violet", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Divider(label="Research target"),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.Select(
                    id="grid-strategy-select", label="Strategy",
                    data=[{"label": item["label"], "value": item["value"]} for item in strategies],
                    value="combo", clearable=False, **PERSIST,
                ),
                dmc.Select(
                    id="grid-symbol-select", label="Symbol", data=symbols,
                    value=default_symbol, clearable=False, searchable=True, **PERSIST,
                ),
                dmc.Select(
                    id="grid-timeframe-select", label="Timeframe",
                    data=[{"label": tf.upper(), "value": tf} for tf in combo["timeframes"]],
                    value=combo["timeframes"][0], clearable=False,
                ),
            ]),
            dmc.Divider(label="Capital and execution"),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.NumberInput(
                    id="grid-balance-input", label="Starting balance (USD)",
                    value=100000, min=1, step=1000, suffix=" USD", thousandSeparator=",",
                    debounce=INPUT_DEBOUNCE_MS, **PERSIST,
                ),
                dmc.NumberInput(
                    id="grid-risk-input", label="Risk per trade",
                    value=0.5, min=0.01, step=0.01, suffix=" %",
                    debounce=INPUT_DEBOUNCE_MS, **PERSIST,
                ),
                dmc.NumberInput(
                    id="grid-parallel-input", label="Parallel CLI workers",
                    value=1, min=1, max=16, step=1, debounce=INPUT_DEBOUNCE_MS, **PERSIST,
                ),
            ]),
            dmc.Text(
                "Parallel workers execute selected combinations concurrently. Default: 1; safety cap: 16.",
                size="xs", c="dimmed",
            ),
            status_region("grid-history-status"),
            dmc.SimpleGrid(cols=2, spacing="md", children=[
                dmc.DateInput(
                    id="grid-start-input", label="Start date", value="2025-01-01",
                    valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=today,
                    clearable=False,
                ),
                dmc.DateInput(
                    id="grid-end-input", label="End date", value=today,
                    valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=today,
                    clearable=False,
                ),
            ]),
            dmc.Divider(label="Parameter space"),
            dmc.SimpleGrid(cols=2, spacing="md", children=[
                _parameter_picker(
                    title="kSL Fibonacci levels",
                    description="Select the stop-loss levels included in this Grid.",
                    full_id="grid-ksl-full",
                    select_id="grid-ksl-select",
                    options=combo["ksl_options"],
                    values=all_ksl,
                ),
                _parameter_picker(
                    title="kTP Fibonacci levels",
                    description="Select the take-profit levels included in this Grid.",
                    full_id="grid-ktp-full",
                    select_id="grid-ktp-select",
                    options=combo["ktp_options"],
                    values=all_ktp,
                ),
            ]),
            html.Div(id="grid-workload-summary"),
            dmc.Group([
                dmc.Button("Create Grid draft (does not run)", id="grid-create-btn", color="blue"),
                dcc.Loading(status_region("grid-create-message"), type="circle"),
            ], align="center"),
        ])),
        dmc.Paper(withBorder=True, radius="md", p="md", mt="md", children=dmc.Stack([
            dmc.Group([
                dmc.Title("Saved Grid sessions", order=4),
                dmc.Button("Refresh sessions", id="grid-refresh-btn", variant="light", size="xs"),
            ], justify="space-between", align="center"),
            dmc.Select(
                id="grid-session-select", label="Select a Grid session", data=[],
                placeholder="No Grid sessions yet", clearable=True, searchable=True,
            ),
            dmc.Text(
                "A Grid session is one complete research batch. Completed or cached sessions can be reopened "
                "without calling cTrader again.",
                size="xs", c="dimmed",
            ),
            dmc.Group([
                dmc.Button("Run selected Grid", id="grid-run-btn", color="orange"),
                dmc.Button("Stop active Grid", id="grid-stop-btn", color="red", disabled=True),
                dmc.Button(
                    "Delete selected Grid", id="grid-delete-btn",
                    color="red", variant="outline", disabled=True,
                ),
            ]),
            dmc.Progress(id="grid-progress-bar", value=0, striped=True, animated=True),
            dmc.Text(id="grid-progress-label", children="No Grid session selected", size="sm", c="dimmed"),
            status_region("grid-run-message"),
            status_region("grid-stop-message"),
            dmc.Text(
                "Delete removes dashboard metadata only; core reports and pipeline caches are retained.",
                size="xs", c="dimmed",
            ),
            status_region("grid-delete-message"),
        ])),
        html.Div(id="grid-session-detail", style={"marginTop": "1rem"}),
    ])


def select_data(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "value": item["session_id"],
            "label": f"{item['title']} - {signal_sources.label_from_input((item.get('plan') or {}).get('input'))} - {item['state']} - {item['created_utc'][:19]}",
        }
        for item in sessions
    ]


def workload_summary(
    strategy: dict[str, Any], workers: Any,
    ksl_levels: list[str] | None, ktp_levels: list[str] | None,
) -> Any:
    ksl_count = len(ksl_levels or [])
    ktp_count = len(ktp_levels or [])
    total = ksl_count * ktp_count
    try:
        worker_count = max(1, int(workers))
    except (TypeError, ValueError):
        worker_count = 1
    waves = (total + worker_count - 1) // worker_count if total else 0
    return dmc.Alert(
        dmc.Group([
            _workload_item("Selected kSL levels", f"{ksl_count:,}"),
            _workload_item("Selected kTP levels", f"{ktp_count:,}"),
            _workload_item("Backtest combinations", f"{total:,}"),
            _workload_item("Concurrent workers", f"{worker_count:,}"),
            _workload_item("Minimum worker waves", f"{waves:,}"),
        ], justify="space-between", align="center"),
        title="Workload preview",
        color="red" if total == 0 else "orange" if total >= 100 else "blue",
        variant="light",
    )


def detail(
    session: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    options: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
) -> Any:
    if not session:
        return dmc.Paper(withBorder=True, radius="md", p="xl", children=dmc.Text(
            "Create or select a Grid session to view its immutable inputs and results.", c="dimmed",
        ))
    snapshot = snapshot or {}
    plan = session.get("plan") or {}
    input_doc = plan.get("input") or {}
    preflight = plan.get("preflight") or {}
    state = str(session.get("state") or "unknown")
    state_color = {
        "draft": "gray", "running": "yellow", "completed": "green",
        "cached": "teal", "cancelled": "orange", "failed": "red",
    }.get(state, "gray")
    # Live progress lives only in the status area above this panel, which is
    # the part the 1-second ticker updates; a copy here would go stale.
    sections: list[Any] = [
        dmc.Group([
            dmc.Stack([
                dmc.Title(str(session.get("title") or "Grid Search"), order=3),
                dmc.Text(f"Session ID: {session.get('session_id')}", size="sm", c="dimmed"),
            ], gap="xs"),
            dmc.Badge(state.upper(), color=state_color, size="lg"),
        ], justify="space-between", align="flex-start"),
        dmc.Divider(label="Grid configuration"),
        _configuration(input_doc, preflight, options),
    ]
    if session.get("error_text"):
        sections.append(dmc.Alert(str(session["error_text"]), title="Grid session error", color="red"))
    ranked = snapshot.get("ranked") or []
    if ranked:
        sections.extend([
            dmc.Divider(label="Grid results"),
            _result_summary(snapshot, input_doc),
            _heatmap(ranked, snapshot.get("rows") or [], input_doc, options),
            _selection_caution(diagnostics),
            _leaderboard(ranked, input_doc, options),
        ])
    elif state == "running":
        sections.append(dmc.Alert(
            "The Grid is running. Live progress is shown above; use Refresh sessions when you want to load "
            "the latest completed combinations into the result view.",
            title="Grid in progress", color="yellow", variant="light",
        ))
    else:
        sections.append(dmc.Text("No eligible Grid result is available yet.", c="dimmed"))
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))


def _workload_item(label: str, value: str) -> Any:
    return dmc.Stack([dmc.Text(label, size="xs", c="dimmed"), dmc.Text(value, fw=700)], gap=1)


def _parameter_picker(
    *, title: str, description: str, full_id: str, select_id: str,
    options: list[dict[str, Any]], values: list[str],
) -> Any:
    return dmc.Paper(
        withBorder=True, radius="sm", p="md", style={"height": "100%"},
        children=dmc.Stack([
        dmc.Group([
            dmc.Stack([
                dmc.Text(title, fw=600, size="sm"),
                dmc.Text(description, size="xs", c="dimmed"),
            ], gap=2),
            dmc.Checkbox(
                id=full_id, label="Full range", checked=True,
                color="blue", size="sm",
            ),
        ], justify="space-between", align="flex-start"),
        dmc.MultiSelect(
            id=select_id,
            data=options,
            value=values,
            disabled=True,
            clearable=True,
            searchable=True,
            placeholder="Choose one or more Fibonacci levels",
        ),
        ], gap="sm"),
    )


def _configuration(input_doc: dict[str, Any], preflight: dict[str, Any], options: dict[str, Any]) -> Any:
    strategy_value = str(input_doc.get("strategy") or "")
    strategy = next(
        (item for item in options.get("strategies", []) if item.get("value") == strategy_value), {}
    )
    cards = [
        ("Strategy", strategy.get("label") or strategy_value),
        ("Symbol", input_doc.get("symbol")),
        ("Timeframe", str(input_doc.get("timeframe") or "-").upper()),
        ("Period", f"{input_doc.get('start', '-')} to {input_doc.get('end', '-') }"),
        ("Starting balance", _number(input_doc.get("balance"), "money")),
        ("Risk per trade", _number((input_doc.get("fixed_params") or {}).get("RiskPercent"), "percent")),
        ("Signal source", signal_sources.label_from_input(input_doc)),
        (
            "Selected kSL levels",
            _selected_level_summary(
                strategy.get("ksl_options") or [],
                (input_doc.get("parameter_space") or {}).get("KslLevel") or [],
            ),
        ),
        (
            "Selected kTP levels",
            _selected_level_summary(
                strategy.get("ktp_options") or [],
                (input_doc.get("parameter_space") or {}).get("KtpLevel") or [],
            ),
        ),
        ("Combinations", _number(input_doc.get("pass_count"), "integer")),
        ("Parallel CLI workers", _number(input_doc.get("max_parallel"), "integer")),
        ("Data mode", str(preflight.get("data_mode") or "-").upper()),
        ("Algorithm", Path(str(preflight.get("algo_path") or "")).name or "-"),
    ]
    return dmc.SimpleGrid(cols=4, spacing="xs", children=[
        dmc.Paper(withBorder=True, radius="sm", p="sm", children=[
            dmc.Text(label, size="xs", c="dimmed"),
            dmc.Text(str(value or "-"), fw=600),
        ])
        for label, value in cards
    ])


def _result_summary(snapshot: dict[str, Any], input_doc: dict[str, Any]) -> Any:
    counts = snapshot.get("counts") or {}
    planned = int(input_doc.get("pass_count") or 0)
    eligible = len(snapshot.get("ranked") or [])
    return dmc.SimpleGrid(cols=4, spacing="xs", children=[
        _stat_card("Planned", f"{planned:,}", "parameter combinations"),
        _stat_card("Eligible", f"{eligible:,}", "valid results for the selected rules"),
        _stat_card("Successful", f"{int(counts.get('ok', 0)):,}", "core status: ok"),
        _stat_card("Other outcomes", f"{sum(v for k, v in counts.items() if k != 'ok'):,}", "skipped, failed or cancelled"),
    ])


def _stat_card(label: str, value: str, note: str) -> Any:
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Text(label, size="xs", c="dimmed"),
        dmc.Text(value, fw=700, size="lg"),
        dmc.Text(note, size="xs", c="dimmed"),
    ], gap=2))


def heatmap_figure(
    rows: list[dict[str, Any]], all_rows: list[dict[str, Any]],
    input_doc: dict[str, Any], options: dict[str, Any],
) -> go.Figure:
    strategy = _strategy_options(str(input_doc.get("strategy") or ""), options)
    ksl_options = strategy.get("ksl_options") or []
    ktp_options = strategy.get("ktp_options") or []
    parameter_space = input_doc.get("parameter_space") or {}
    selected_ksl = {str(value) for value in parameter_space.get("KslLevel") or []}
    selected_ktp = {str(value) for value in parameter_space.get("KtpLevel") or []}
    ksl_values = [str(item["value"]) for item in ksl_options if str(item["value"]) in selected_ksl]
    ktp_values = [str(item["value"]) for item in ktp_options if str(item["value"]) in selected_ktp]
    values: dict[tuple[str, str], Any] = {}
    hovers: dict[tuple[str, str], str] = {}
    for row in rows:
        params = row.get("params") or {}
        key = (str(params.get("KslLevel")), str(params.get("KtpLevel")))
        # Cut to whole USD (dashboard rule: cut, never round) before the cell
        # ever reaches Plotly's own "%{z:,.0f}" formatting.
        values[key] = truncated(row.get("net_profit"), 0)
        hovers[key] = (
            f"kSL: {_option_label(ksl_options, key[0])}<br>"
            f"kTP: {_option_label(ktp_options, key[1])}<br>"
            f"Net profit: {_number(row.get('net_profit'), 'money')}<br>"
            f"Profit factor: {_number(row.get('profit_factor_resolved'), 'number')}<br>"
            f"Max equity drawdown: {_number(row.get('max_equity_drawdown_pct'), 'percent')}"
        )
    ran = {
        (str((row.get("params") or {}).get("KslLevel")), str((row.get("params") or {}).get("KtpLevel")))
        for row in all_rows
    }
    excluded = [key for key in ran if key not in values and key[0] in selected_ksl and key[1] in selected_ktp]
    x_labels = [_option_label(ktp_options, value) for value in ktp_values]
    y_labels = [_option_label(ksl_options, value) for value in ksl_values]

    def gap_text(key: tuple[str, str]) -> str:
        where = f"kSL: {_option_label(ksl_options, key[0])}<br>kTP: {_option_label(ktp_options, key[1])}<br>"
        if key in ran:
            return where + "Ran, but excluded by the strict research filter"
        return where + "Not run yet"

    figure = go.Figure(go.Heatmap(
        z=[[values.get((ksl, ktp)) for ktp in ktp_values] for ksl in ksl_values],
        x=x_labels,
        y=y_labels,
        customdata=[[hovers.get((ksl, ktp)) or gap_text((ksl, ktp)) for ktp in ktp_values] for ksl in ksl_values],
        hovertemplate="%{customdata}<extra></extra>",
        colorscale=PROFIT_COLORSCALE,
        zmid=0,
        texttemplate="%{z:,.0f}",
        textfont={"size": 10},
        colorbar={"title": "Net profit (USD)"},
        hoverongaps=True,
    ))
    if excluded:
        figure.add_trace(go.Scatter(
            x=[_option_label(ktp_options, key[1]) for key in excluded],
            y=[_option_label(ksl_options, key[0]) for key in excluded],
            mode="text", text=["✕"] * len(excluded), textfont={"size": 14, "color": "#495057"},
            hoverinfo="skip", showlegend=False,
        ))
    figure.update_layout(
        template="plotly_white", margin={"l": 90, "r": 30, "t": 20, "b": 80},
        xaxis_title="kTP Fibonacci level", yaxis_title="kSL Fibonacci level",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"categoryorder": "array", "categoryarray": x_labels},
        yaxis={"categoryorder": "array", "categoryarray": y_labels},
    )
    return figure


def _heatmap(
    rows: list[dict[str, Any]], all_rows: list[dict[str, Any]],
    input_doc: dict[str, Any], options: dict[str, Any],
) -> Any:
    figure = heatmap_figure(rows, all_rows, input_doc, options)
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Stack([
                dmc.Text("Net profit heatmap", fw=600, size="sm"),
                dmc.Text(
                    "Every cell is one independent cTrader backtest. Colour is centred on 0 USD: "
                    "red is a loss, blue a profit. Blank = not run yet; ✕ = ran but excluded by the "
                    "strict research filter.",
                    size="xs", c="dimmed",
                ),
            ], gap=2),
            dmc.Badge(f"{len(rows):,} eligible", color="green", variant="light"),
        ], justify="space-between"),
        dcc.Graph(
            figure=figure,
            config={"displaylogo": False, "displayModeBar": False, "responsive": True},
            style={"height": "34rem"},
        ),
    ], gap="xs"))


LEADERBOARD_NUMERIC = ("rank", "net_profit", "profit_factor", "trades", "win_rate", "drawdown", "wall")


def leaderboard_table(rows: list[dict[str, Any]], input_doc: dict[str, Any], options: dict[str, Any]) -> Any:
    strategy = _strategy_options(str(input_doc.get("strategy") or ""), options)
    ksl_options = strategy.get("ksl_options") or []
    ktp_options = strategy.get("ktp_options") or []
    data = []
    for rank, row in enumerate(rows, start=1):
        params = row.get("params") or {}
        data.append({
            "rank": rank,
            "ksl": _option_label(ksl_options, str(params.get("KslLevel"))),
            "ktp": _option_label(ktp_options, str(params.get("KtpLevel"))),
            # Cut, never round (dashboard display rule), before each Format
            # (MONEY/PERCENT/DECIMAL/SECONDS below) renders with 2 decimals.
            "net_profit": truncated(row.get("net_profit")),
            "profit_factor": truncated(row.get("profit_factor_resolved")),
            "trades": truncated(row.get("total_trades"), 0),
            "win_rate": truncated(_win_rate_percent(row.get("win_rate"))),
            "drawdown": truncated(row.get("max_equity_drawdown_pct")),
            "wall": truncated(row.get("wall_seconds")),
        })
    columns = [
        numeric_column("rank", "Rank", INTEGER),
        {"id": "ksl", "name": "kSL"}, {"id": "ktp", "name": "kTP"},
        numeric_column("net_profit", "Net profit", MONEY),
        numeric_column("profit_factor", "Profit factor", DECIMAL),
        numeric_column("trades", "Trades", INTEGER),
        numeric_column("win_rate", "Win rate", PERCENT),
        numeric_column("drawdown", "Max equity DD", PERCENT),
        numeric_column("wall", "Runtime", SECONDS),
    ]
    return dash_table.DataTable(
        columns=columns,
        data=data, page_action="native", page_size=15, sort_action="native",
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
        style_cell_conditional=right_align(list(LEADERBOARD_NUMERIC)),
        # Sign drives colour (a loss must never read as a gain) and the minus
        # sign stays in the text, so colour is not the only cue (WCAG 1.4.1).
        style_data_conditional=[
            {"if": {"filter_query": "{net_profit} < 0", "column_id": "net_profit"},
             "color": "#dc2626", "fontWeight": 600},
            {"if": {"filter_query": "{net_profit} >= 0", "column_id": "net_profit"},
             "color": "#15803d", "fontWeight": 600},
        ],
    )


def _leaderboard(rows: list[dict[str, Any]], input_doc: dict[str, Any], options: dict[str, Any]) -> Any:
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Text("Ranked combinations (in-sample)", fw=600, size="sm"),
            dmc.Text("Default order: profit factor, drawdown, then net profit. Click a header to re-sort.",
                     size="xs", c="dimmed"),
        ], justify="space-between"),
        leaderboard_table(rows, input_doc, options),
    ], gap="xs"))


def _selection_caution(diagnostics: dict[str, Any] | None) -> Any:
    """Put the overfitting evidence next to the ranking a user might pick from.

    Bailey et al. (PBO/CSCV) show the expected out-of-sample rank of the best
    in-sample configuration declines sharply as more configurations are tried.
    """
    result = (diagnostics or {}).get("result") or {}
    if (diagnostics or {}).get("state") in {"completed", "cached"} and result.get("pbo") and result.get("dsr"):
        pbo_value = float(result["pbo"].get("pbo") or 0)
        dsr_value, dsr_basis = selected_dsr(result["dsr"])
        pbo_percent = truncated(pbo_value * 100)
        dsr_percent = truncated(dsr_value * 100)
        return dmc.Alert(
            f"DSR + PBO for this Grid: probability of backtest overfitting {pbo_percent:.2f}%, "
            f"deflated Sharpe confidence {dsr_percent:.2f}% for the {dsr_basis}. "
            "The ranking below is in-sample only.",
            title="Overfitting diagnostics", color="orange" if pbo_value > 0.5 else "blue", variant="light",
        )
    return dmc.Alert(
        "This ranking is in-sample only. The best in-sample combination tends to rank lower out of "
        "sample as more combinations are tried, so run DSR + PBO on this Grid before choosing parameters.",
        title="Not yet assessed for overfitting", color="orange", variant="light",
    )


def _strategy_options(value: str, options: dict[str, Any]) -> dict[str, Any]:
    return next((item for item in options.get("strategies", []) if item.get("value") == value), {})


def _option_label(options: list[dict[str, Any]], value: str) -> str:
    return str(next((item.get("label") for item in options if str(item.get("value")) == value), value))


def _level_range(options: list[dict[str, Any]]) -> str:
    if not options:
        return "-"
    return f"{options[0]['label']} to {options[-1]['label']} ({len(options)} levels)"


def _selected_level_summary(options: list[dict[str, Any]], selected: list[Any]) -> str:
    values = [str(value) for value in selected]
    labels = [_option_label(options, value) for value in values]
    if not labels:
        return "-"
    if len(labels) == len(options):
        return f"Full range: {_level_range(options)}"
    return ", ".join(labels)


def _win_rate_percent(value: Any) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number * 100 if abs(number) <= 1 else number


def _number(value: Any, kind: str) -> str:
    if value in (None, ""):
        return "-"
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "-"
        if kind == "integer":
            text = f"{int(number):,}"
        elif number == number.to_integral_value():
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
