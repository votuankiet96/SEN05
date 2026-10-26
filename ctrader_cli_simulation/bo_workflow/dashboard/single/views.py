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
    from ..ui_common import (
        DECIMAL, INPUT_DEBOUNCE_MS, INTEGER, MONEY, PERSIST, STRATEGY_PERSIST,
        numeric_column, right_align, status_region, strategy_label, truncated,
    )
except ImportError:  # app.py loads feature folders as top-level modules.
    import signal_sources
    from ui_common import (
        DECIMAL, INPUT_DEBOUNCE_MS, INTEGER, MONEY, PERSIST, STRATEGY_PERSIST,
        numeric_column, right_align, status_region, strategy_label, truncated,
    )

# Display resolution for long equity curves. M4 aggregation keeps the first,
# last, minimum and maximum point per time bucket, which renders line charts
# pixel-identically when buckets >= plot width (Jugel et al., "M4", VLDB 2014).
# 1,200 buckets covers the widest Mantine "xl" container.
EQUITY_DISPLAY_BUCKETS = 1_200
BOT_ACTIVITY_PAGE_SIZE = 20
HISTORY_DISPLAY_LIMIT = 500


def layout(options: dict[str, Any]) -> html.Div:
    symbols = options["symbols"]
    default_symbol = next(
        (item["value"] for item in symbols if item["value"] == "GER40.cash"),
        symbols[0]["value"],
    )
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
                        "Each session is fixed to exactly 1 symbol, 1 Redis source, 16 channels and 17 periods "
                        "(272 isolated cells). Creating a session only reads Redis/SQL to build the run card; the cTrader CLI is not called.",
                        c="dimmed",
                    ),
                ], gap="xs"),
                dmc.Badge("Standard flow v2", color="blue", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Divider(),
            dmc.Select(
                id="session-symbol-select", label="Symbol",
                data=symbols, value=default_symbol,
                clearable=False, searchable=True, **PERSIST,
            ),
            dmc.Text(
                "The signal source comes from Signal source at the top of the page: a job uses either db0 or db1. "
                "Engine, balance, Fibonacci, risk, isolated, from2022 and max_parallel are resolved from config.yaml.",
                size="xs", c="dimmed",
            ),
            dmc.Group([
                dmc.Button("Create run card (no CLI run)", id="create-session-btn", color="blue"),
                dcc.Loading(status_region("session-create-message"), type="circle"),
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
                placeholder="Select a session", clearable=True, searchable=True,
            ),
            dmc.Text(
                "Select one completed or cached session to inspect it. Select one or more drafts to queue them "
                "in order. The dashboard claims only one session at a time; inside a v2 session, the core uses max_parallel from config.",
                size="xs", c="dimmed",
            ),
            dmc.Group([
                dcc.ConfirmDialogProvider(
                    id="session-run-confirm",
                    message=(
                        "Run the selected session according to the displayed run card? "
                        "This may call the cTrader CLI and consume the daily quota."
                    ),
                    children=dmc.Button("Confirm and run", id="run-session-btn", color="orange"),
                ),
                dmc.Button("Stop active run", id="stop-session-btn", color="red", disabled=True),
                dmc.Button(
                    "Delete selected sessions", id="delete-selected-session-btn",
                    color="red", variant="outline", disabled=True,
                ),
            ], align="end"),
            html.Div(id="session-active-run"),
            dmc.Progress(id="session-progress-bar", value=0, striped=True, animated=True),
            dmc.Text(id="session-progress-label", children="No active run", size="sm", c="dimmed"),
            status_region("session-run-message"),
            status_region("session-stop-message"),
            dmc.Text(
                "Delete removes dashboard metadata only; core reports and pipeline caches are retained.",
                size="xs", c="dimmed",
            ),
            status_region("session-delete-message"),
        ])),
        html.Div(id="session-detail", style={"marginTop": "1rem"}),
    ])


def active_run_summary(session: dict[str, Any] | None) -> Any:
    """Compact, live run context that does not depend on the selected result."""
    if not session:
        return None
    elapsed = _duration_since(session.get("started_utc"))
    last_update = _duration_since(session.get("updated_utc"))
    progress = float(session.get("progress_pct") or 0)
    stage = str(session.get("progress_text") or "Running")
    if progress <= 0:
        progress_label = f"{stage} - waiting for a CLI progress percentage"
    else:
        progress_label = f"{_format_value(progress, 'number')}% - {stage}"
    return dmc.Alert(
        title="Active backtest",
        color="blue",
        variant="light",
        children=dmc.Stack([
            dmc.Text(str(session.get("title") or session.get("session_id") or ""), fw=600),
            dmc.Group([
                dmc.Text(f"Elapsed: {elapsed}"),
                dmc.Text(f"CLI progress: {progress_label}"),
                dmc.Text(f"Last status update: {last_update} ago"),
            ], gap="lg"),
        ], gap="xs"),
    )


def _duration_since(value: Any) -> str:
    if not value:
        return "unknown"
    try:
        when = datetime.fromisoformat(str(value))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


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
    if any((session.get("plan") or {}).get("schema") != "bo-dashboard-single-backtest/v2" for session in sessions):
        return dmc.Alert(
            "Legacy v1 sessions are read-only and cannot be queued to run again. Select one on its own to view its results, "
            "or create a new v2 session.",
            title="Legacy session is read-only", color="orange", variant="light",
        )
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
    if plan.get("schema") == "bo-dashboard-single-backtest/v2":
        return _standard_detail(session, execution, events)
    input_doc = plan.get("input") or {}
    preflight = plan.get("preflight") or {}
    header = dmc.Group([
        dmc.Stack([
            dmc.Title(session["title"], order=3),
            dmc.Text(f"Session ID: {session['session_id']}", size="sm", c="dimmed"),
        ], gap="xs"),
        dmc.Badge(state.upper(), color=state_color, size="lg"),
    ], justify="space-between", align="flex-start")
    sections: list[Any] = [header]
    if plan.get("schema") == "bo-dashboard-single-backtest/v1":
        sections.append(dmc.Alert(
            "This is a v1 session from the legacy flow. The dashboard only lets you read its results; to run again, create a new v2 session.",
            title="Legacy session is read-only", color="orange", variant="light",
        ))
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
        sections.extend(_execution_sections(execution, str(session.get("experiment") or "")))
    elif state == "running":
        sections.append(dmc.Alert(
            "The core engine is running. Live progress and Stop are shown above; "
            "successful-run logs appear after the core engine finishes writing artifacts.",
            title="Running", color="yellow", variant="light",
        ))
    elif state in {"completed", "cached"}:
        sections.append(dmc.Alert(
            "The result has been deleted: the session metadata remains but the execution artifact is no longer on disk.",
            title="Result no longer exists", color="orange", variant="light",
        ))
    else:
        sections.append(dmc.Text("No execution artifact exists for this session yet.", c="dimmed"))

    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))


def _session_event_table(events: list[dict[str, Any]]) -> Any:
    rows = [{
        "time": event.get("at_utc"),
        "event": str(event.get("kind") or "unknown").replace("_", " ").title(),
        "detail": event.get("detail") or "--",
    } for event in events]
    return dmc.Stack([
        dmc.Divider(label="Session lifecycle"),
        dash_table.DataTable(
            columns=[
                {"id": "time", "name": "Time (UTC)"},
                {"id": "event", "name": "Event"},
                {"id": "detail", "name": "Detail"},
            ],
            data=rows,
            page_action="native",
            page_size=20,
            sort_action="native",
            style_table={"overflowX": "auto"},
            style_header={"fontWeight": 600, "backgroundColor": "#f8fafc"},
            style_cell={"padding": "8px", "fontFamily": "system-ui", "fontSize": 13},
        ),
    ], gap="xs")


def _standard_detail(
    session: dict[str, Any], execution: dict[str, Any], events: list[dict[str, Any]],
) -> Any:
    """Render the v2 run card and every strategy/timeframe/period cell."""
    plan = session.get("plan") or {}
    input_doc = plan.get("input") or {}
    preflight = plan.get("preflight") or {}
    state = str(session.get("state") or "unknown")
    state_color = {
        "draft": "gray", "running": "yellow", "completed": "green",
        "cached": "teal", "cancelled": "orange", "failed": "red",
    }.get(state, "gray")
    sections: list[Any] = [
        dmc.Group([
            dmc.Stack([
                dmc.Title(str(session.get("title") or "Single Backtest"), order=3),
                dmc.Text(f"Session ID: {session.get('session_id')}", size="sm", c="dimmed"),
            ], gap="xs"),
            dmc.Badge(state.upper(), color=state_color, size="lg"),
        ], justify="space-between", align="flex-start"),
        dmc.Divider(label="Frozen run card"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, spacing="sm", children=[
            _standard_card("Total cells", f"{int(preflight.get('total_count') or 0):,}", "16 channels × 17 periods"),
            _standard_card(
                "Existing", f"{int(preflight.get('existing_count') or 0):,}",
                f"local {int(preflight.get('existing_local_count') or 0):,} · SQL-only {int(preflight.get('existing_sql_only_count') or 0):,}",
            ),
            _standard_card("New runs", f"{int(preflight.get('new_count') or 0):,}", "within the 272 logical cells"),
            _standard_card(
                "CLI required / remaining",
                f"{int(preflight.get('cli_required') or 0):,} / {int(preflight.get('cli_remaining') or 0):,}",
                f"used {int(preflight.get('cli_used') or 0):,}/{int(preflight.get('cli_limit') or 0):,} · Prague",
                "#16a34a" if preflight.get("budget_ok") else "#dc2626",
            ),
        ]),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, spacing="sm", children=[
            _standard_card("Symbol", str(input_doc.get("symbol") or "—")),
            _standard_card("Source", signal_sources.label_from_input(input_doc), "1 source per job"),
            _standard_card("Engine", str(preflight.get("engine_profile") or "—")),
            _standard_card(
                "Mode",
                f"{preflight.get('account_mode', '—')} · {preflight.get('period_group', '—')}",
                f"max_parallel={preflight.get('max_parallel', '—')}",
            ),
        ]),
        dmc.Alert(
            (
                "The Combo H1 warm-up (2022-01-01 → 2026-06-30) runs first with max_parallel=1. "
                "This is at most 1 extra CLI run outside the 272-cell matrix, because the core labels the warm-up window as single."
                if preflight.get("warmup_required")
                else "The Combo H1 warm-up (2022-01-01 → 2026-06-30) already has a result, so it will not run again."
            ),
            title="Warm-up",
            color="blue",
            variant="light",
        ),
        dmc.Text(
            "The run button requires confirmation. Legacy v1 sessions are read-only; the dashboard does not rerun them through the legacy flow.",
            size="xs", c="dimmed",
        ),
    ]
    if session.get("error_text"):
        sections.append(dmc.Alert(str(session["error_text"]), title="Session error", color="red"))
    if events:
        sections.append(_session_event_table(events))
    if execution.get("read_error"):
        sections.append(dmc.Alert(execution["read_error"], title="Could not read result", color="red"))
    if execution.get("read_warning"):
        sections.append(dmc.Alert(execution["read_warning"], title="SQL read warning", color="orange"))

    cells = list(execution.get("cells") or [])
    available = int(execution.get("available") or 0)
    if cells and available:
        sections.extend([
            dmc.Divider(label=f"Results by cell · {available}/{len(cells)} with data"),
            dmc.Text(
                "Each cell: Net Profit / Max Equity DD %. An asterisk (*) = has margin rejection; “= db0” = db1 has identical content and uses the db0 row.",
                size="xs", c="dimmed",
            ),
            *_standard_result_tables(cells),
            dmc.Text(
                "PF=0 when there are no losing trades is a data storage convention and must not be read as a poor PF.",
                size="xs", c="dimmed",
            ),
        ])
    elif state in {"completed", "cached"}:
        sections.append(dmc.Alert(
            "The result has been deleted: no local artifact or SQL row matches this session's run_keys any more.",
            title="Result no longer exists", color="orange", variant="light",
        ))
    elif state == "running":
        sections.append(dmc.Alert(
            "The core is running; the per-cell tables will appear once results are written.",
            title="Running", color="yellow", variant="light",
        ))
    else:
        sections.append(dmc.Text("No results yet; this is only the run card.", c="dimmed"))
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))


def _standard_card(label: str, value: str, note: str = "", accent: str | None = None) -> Any:
    style = {"borderTop": f"3px solid {accent}"} if accent else None
    return dmc.Paper(withBorder=True, radius="sm", p="sm", style=style, children=dmc.Stack([
        dmc.Text(label, size="xs", c="dimmed", fw=500),
        dmc.Text(value, fw=700),
        dmc.Text(note, size="xs", c="dimmed") if note else html.Span(),
    ], gap=2))


def _standard_result_tables(cells: list[dict[str, Any]]) -> list[Any]:
    strategy_labels = {"combo": "Combo", "ma_cross": "MA Cross", "macd_break": "MACD Break"}
    windows = list(dict.fromkeys(str(cell.get("window") or "") for cell in cells))
    result: list[Any] = []
    for strategy in ("combo", "ma_cross", "macd_break"):
        strategy_cells = [cell for cell in cells if cell.get("strategy") == strategy]
        if not strategy_cells:
            continue
        timeframes = list(dict.fromkeys(str(cell.get("timeframe") or "") for cell in strategy_cells))
        indexed = {
            (str(cell.get("timeframe") or ""), str(cell.get("window") or "")): cell
            for cell in strategy_cells
        }
        rows = []
        for timeframe in timeframes:
            row = {"timeframe": timeframe}
            for window in windows:
                row[window] = _standard_cell_text(indexed.get((timeframe, window)))
            rows.append(row)
        columns = [{"name": "Timeframe", "id": "timeframe"}] + [
            {"name": window, "id": window} for window in windows
        ]
        result.extend([
            dmc.Title(strategy_labels[strategy], order=5),
            dash_table.DataTable(
                data=rows,
                columns=columns,
                style_table={"overflowX": "auto"},
                style_cell={
                    "fontFamily": "monospace", "fontSize": 12, "padding": "6px",
                    "minWidth": "118px", "textAlign": "right", "whiteSpace": "nowrap",
                },
                style_header={"fontWeight": "bold", "backgroundColor": "#f8fafc"},
                style_cell_conditional=[{
                    "if": {"column_id": "timeframe"}, "textAlign": "left", "minWidth": "72px",
                }],
            ),
        ])
    return result


def _standard_cell_text(cell: dict[str, Any] | None) -> str:
    if not cell or cell.get("status") not in {"ok", "skipped"}:
        return "—"
    profit = _finite_float(cell.get("net_profit"))
    drawdown = _finite_float(cell.get("max_equity_drawdown_pct"))
    profit_text = "—" if profit is None else f"{profit:,.2f}"
    drawdown_text = "—" if drawdown is None else f"{drawdown:,.2f}%"
    try:
        rejected = int(cell.get("margin_rejections") or 0) > 0
    except (TypeError, ValueError):
        rejected = False
    suffix = "*" if rejected else ""
    if cell.get("fallback_db0"):
        suffix += " (= db0)"
    return f"{profit_text} / {drawdown_text}{suffix}"


def _execution_sections(
    execution: dict[str, Any], experiment: str, *, activity_prefix: str = "session",
) -> list[Any]:
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
        result.append(_bot_activity_table(bot_events, experiment, component_prefix=activity_prefix))
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
    cards = (
        ("Strategy", strategy_label(strategy, form_options)),
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
            # This is an inspection chart, not an editing surface (user
            # requirement: synchronised hover only, no zoom). Keep its time
            # range stable so a hover cannot be mistaken for a zoom/pan.
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
    # Drawdown comes from every report point; only the display is aggregated,
    # and M4 keeps each bucket's extreme, so the maximum drawdown stays visible.
    drawdown = _drawdown_series(balance, min_equity, max_equity)
    epoch = [_epoch_ms(item.get("timestamp") or item.get("time") or item.get("date")) for item in points]
    balance_keep = _m4_indices(epoch, balance, EQUITY_DISPLAY_BUCKETS)
    drawdown_keep = _m4_indices(epoch, drawdown, EQUITY_DISPLAY_BUCKETS)
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.60, 0.40], vertical_spacing=0.07,
    )
    # Values are formatted by Plotly's hovertemplate instead of shipping a
    # pre-formatted string per point; "x unified" hover lists both traces.
    # The plotted values are cut to 2 decimals first, so ",.2f" shows the cut
    # digits (dashboard rule: cut, never round) — invisible at pixel level.
    figure.add_trace(go.Scatter(
        x=[x[i] for i in balance_keep], y=[truncated(balance[i]) for i in balance_keep],
        mode="lines", name="Account value",
        line={"width": 2.5, "color": "#2563eb", "shape": "hv"},
        fill="tozeroy", fillcolor="rgba(37, 99, 235, 0.08)",
        hovertemplate="<b>Account value</b>: %{y:,.2f} USD<extra></extra>",
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=[x[i] for i in drawdown_keep], y=[truncated(drawdown[i]) for i in drawdown_keep],
        mode="lines", name="Equity drawdown",
        line={"width": 1.5, "color": "#dc2626"},
        fill="tozeroy", fillcolor="rgba(220, 38, 38, 0.18)",
        hovertemplate="<b>Equity drawdown</b>: %{y:.2f}%<extra></extra>",
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


def _epoch_ms(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp() * 1000


def _m4_indices(times: list[float | None], values: list[Any], buckets: int) -> list[int]:
    """Indices kept by M4 aggregation (first, last, min, max per time bucket).

    Series short enough to render every point, or without usable timestamps,
    are returned unchanged.
    """
    count = len(values)
    known = [t for t in times if t is not None]
    if count <= 4 * buckets or len(known) != count or max(known) <= min(known):
        return list(range(count))
    start, span = min(known), max(known) - min(known)
    groups: dict[int, list[int]] = {}
    for index, when in enumerate(times):
        groups.setdefault(min(buckets - 1, int((when - start) / span * buckets)), []).append(index)
    keep: set[int] = set()
    for members in groups.values():
        keep.update((members[0], members[-1]))
        numeric = [i for i in members if _finite_float(values[i]) is not None]
        if numeric:
            keep.add(min(numeric, key=lambda i: float(values[i])))
            keep.add(max(numeric, key=lambda i: float(values[i])))
    return sorted(keep)


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


_HISTORY_NUMERIC = (
    "id", "entry_price", "close_price", "net", "gross", "commissions",
    "swaps", "pips", "volume", "quantity", "balance",
)


def _history_table(history: list[dict[str, Any]]) -> Any:
    total = sum(1 for item in history if isinstance(item, dict))
    raw_rows = [dict(item) for item in history[:HISTORY_DISPLAY_LIMIT] if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        net = truncated(item.get("net"))
        rows.append({
            "id": truncated(item.get("id"), 0),
            "symbol": item.get("symbol"),
            "direction": str(item.get("direction") or "—").upper(),
            "entry_time": _trade_time(item.get("entryTime")),
            "entry_price": truncated(item.get("entryPrice")),
            "close_time": _trade_time(item.get("closeTime")),
            "close_price": truncated(item.get("closePrice")),
            "net": net,
            "gross": truncated(item.get("gross")),
            "commissions": truncated(item.get("commissions")),
            "swaps": truncated(item.get("swaps")),
            "pips": truncated(item.get("pips")),
            "volume": truncated(item.get("volume")),
            "quantity": truncated(item.get("quantity")),
            "balance": truncated(item.get("balance")),
            "label": item.get("label") or "—",
            "comment": item.get("comment") or "—",
        })
    columns = [
        numeric_column("id", "Trade #", INTEGER),
        {"id": "symbol", "name": "Symbol"}, {"id": "direction", "name": "Side"},
        {"id": "entry_time", "name": "Entry time"}, numeric_column("entry_price", "Entry price", DECIMAL),
        {"id": "close_time", "name": "Close time"}, numeric_column("close_price", "Close price", DECIMAL),
        numeric_column("net", "Net P&L", MONEY), numeric_column("gross", "Gross P&L", MONEY),
        numeric_column("commissions", "Commission", MONEY), numeric_column("swaps", "Swap", MONEY),
        numeric_column("pips", "Pips", DECIMAL), numeric_column("volume", "Volume", DECIMAL),
        numeric_column("quantity", "Quantity", DECIMAL), numeric_column("balance", "Balance", MONEY),
        {"id": "label", "name": "Label"}, {"id": "comment", "name": "Comment"},
    ]
    table = dash_table.DataTable(
        columns=columns,
        data=rows,
        page_action="native",
        page_size=12,
        sort_action="native",
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
        style_cell_conditional=right_align(list(_HISTORY_NUMERIC)),
        # Sign drives colour; the minus sign stays in the value, so colour is
        # not the only cue (WCAG 1.4.1).
        style_data_conditional=[
            {"if": {"filter_query": "{net} >= 0", "column_id": "net"}, "color": "#15803d", "fontWeight": 600},
            {"if": {"filter_query": "{net} < 0", "column_id": "net"}, "color": "#dc2626", "fontWeight": 600},
            {"if": {"column_id": "id"}, "color": "#64748b"},
        ],
    )
    shown = (
        f"{total:,} closed trades" if len(rows) == total
        else f"First {len(rows):,} of {total:,} closed trades"
    )
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
        dmc.Group([
            dmc.Text("Trade history", fw=600, size="sm"),
            dmc.Badge(shown, variant="light", color="blue" if len(rows) == total else "orange"),
        ], justify="space-between"),
        table,
    ], gap="xs"))


def bot_activity_rows(events: Any) -> list[dict[str, Any]]:
    """Display rows of the cBot log: exactly the three shown columns, plus the
    tone used for colouring and the original line order used to sort by time."""
    return [
        {
            "time": _bot_event_time(item.get("time")),
            "level": item.get("level") or "—",
            "detail": item.get("detail") or "—",
            "_tone": item.get("tone") or "neutral",
            "_order": order,
        }
        for order, item in enumerate(item for item in events if isinstance(item, dict))
    ]


def _bot_activity_table(
    events: list[dict[str, Any]], experiment: str, *, component_prefix: str = "session",
) -> Any:
    # Only the first page is sent with the detail view; paging, sorting and
    # filtering run on the server (single.callbacks.page_bot_activity), so a
    # long log does not travel to the browser in full.
    rows = bot_activity_rows(events)
    table = dash_table.DataTable(
        id=f"{component_prefix}-bot-activity-table",
        columns=[
            {"name": "Event time", "id": "time"},
            {"name": "Level", "id": "level"},
            {"name": "Message", "id": "detail"},
        ],
        data=rows[:BOT_ACTIVITY_PAGE_SIZE],
        hidden_columns=["_tone", "_order"],
        page_action="custom",
        page_current=0,
        page_size=BOT_ACTIVITY_PAGE_SIZE,
        page_count=max(1, -(-len(rows) // BOT_ACTIVITY_PAGE_SIZE)),
        sort_action="custom",
        sort_mode="single",
        sort_by=[],
        filter_action="custom",
        filter_query="",
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
        dcc.Store(id=f"{component_prefix}-bot-activity-source", data={"experiment": experiment}),
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
