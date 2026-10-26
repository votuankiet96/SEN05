"""Presentation for explicit-row shared-account Multi-channel Backtests."""
from __future__ import annotations

from datetime import date
from typing import Any

from dash import dash_table, dcc, html
import dash_mantine_components as dmc
import plotly.graph_objects as go

try:
    from .. import actions, signal_sources
    from ..ui_common import INPUT_DEBOUNCE_MS, PERSIST, status_region
    from ..single import views as single_views
except ImportError:
    import actions
    import signal_sources
    from ui_common import INPUT_DEBOUNCE_MS, PERSIST, status_region
    from single import views as single_views


# Row value meaning "use the Signal source chosen at the top of the page" (dmc.Select needs a non-empty value).
SOURCE_PAGE_DEFAULT = "default"


def default_channel(options: dict[str, Any], index: int = 0) -> dict[str, Any]:
    strategy = options["strategies"][0]
    symbols = options["symbols"]
    symbol = next((item["value"] for item in symbols if item["value"] == "US30.cash"), symbols[0]["value"])
    channel = {
        "id": index, "strategy": strategy["value"], "symbol": symbol,
        "timeframe": strategy["timeframes"][0],
        "risk_percent": actions.DEFAULT_MULTI_CHANNEL_RISK_PERCENT,
    }
    return actions.allocate_multi_channel_risk(
        [channel], mode=actions.MULTI_CHANNEL_RISK_EQUAL,
        total_percent=options["portfolio_risk"]["total_risk_percent"],
    )["channels"][0]


def _bool_param(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def channel_rows(
    rows: list[dict[str, Any]], options: dict[str, Any],
    risk_mode: str = actions.MULTI_CHANNEL_RISK_EQUAL,
) -> Any:
    strategies = options["strategies"]
    strategy_data = [{"label": item["label"], "value": item["value"]} for item in strategies]
    catalog = {item["value"]: item for item in strategies}
    rendered = []
    for position, row in enumerate(rows, start=1):
        index = int(row["id"])
        strategy = str(row.get("strategy") or strategies[0]["value"])
        frames = catalog.get(strategy, strategies[0])["timeframes"]
        timeframe = str(row.get("timeframe") or frames[0]).lower()
        if timeframe not in frames:
            timeframe = frames[0]
        rendered.append(dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
            dmc.Group([
                dmc.Text(f"Channel {position}", fw=600),
                dmc.Button(
                    "Delete", id={"type": "multi-remove-channel", "index": index},
                    color="red", variant="subtle", size="xs", disabled=len(rows) == 1,
                ),
            ], justify="space-between"),
            dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 5}, spacing="sm", children=[
                dmc.Select(id={"type": "multi-channel-strategy", "index": index}, label="Strategy", data=strategy_data, value=strategy, clearable=False),
                dmc.Select(id={"type": "multi-channel-symbol", "index": index}, label="Symbol", data=options["symbols"], value=row.get("symbol"), clearable=False, searchable=True),
                dmc.Select(
                    id={"type": "multi-channel-timeframe", "index": index}, label="Timeframe",
                    data=[{"label": frame.upper(), "value": frame} for frame in frames],
                    value=timeframe, clearable=False,
                ),
                dmc.Select(
                    id={"type": "multi-channel-source", "index": index}, label="Signal source",
                    data=[{"label": "Page source", "value": SOURCE_PAGE_DEFAULT}, *signal_sources.OPTIONS],
                    value=row.get("source") or SOURCE_PAGE_DEFAULT, clearable=False,
                ),
                dmc.NumberInput(
                    id={"type": "multi-channel-risk", "index": index}, label="Risk per leg group",
                    value=float(row.get("risk_percent") or 0.6), min=0.0001,
                    step=10 ** -int(options["portfolio_risk"]["risk_decimals"]),
                    suffix=" %", debounce=INPUT_DEBOUNCE_MS,
                    disabled=risk_mode == actions.MULTI_CHANNEL_RISK_EQUAL,
                ),
            ]),
        ], gap="xs")))
    return dmc.Stack(rendered, gap="sm")


def layout(options: dict[str, Any]) -> html.Div:
    backtest_defaults = options["backtest_defaults"]
    defaults = backtest_defaults["fixed_params"]
    risk_defaults = options["portfolio_risk"]
    today = date.today().isoformat()
    return html.Div([
        dcc.Interval(id="multi-progress-poll", interval=1_000, n_intervals=0, disabled=True),
        dcc.Store(id="multi-selection-request", data=None),
        dcc.Store(id="multi-run-state", data={"active": False}),
        dcc.Store(id="multi-channel-rows-store", data=[default_channel(options)]),
        dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
            dmc.Group([
                dmc.Stack([
                    dmc.Title("Multi-channel Backtest", order=3),
                    dmc.Text("Each row is an independent channel; all channels share one account within each period.", c="dimmed"),
                ], gap="xs"),
                dmc.Badge("Shared account", color="blue", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Divider(label="Risk allocation"),
            dmc.SimpleGrid(cols={"base": 1, "sm": 2}, spacing="md", children=[
                dmc.Select(
                    id="multi-risk-mode", label="Allocation mode",
                    data=[
                        {"label": "Equal split", "value": actions.MULTI_CHANNEL_RISK_EQUAL},
                        {"label": "Manual", "value": actions.MULTI_CHANNEL_RISK_MANUAL},
                    ],
                    value=actions.MULTI_CHANNEL_RISK_EQUAL, clearable=False,
                ),
                dmc.NumberInput(
                    id="multi-total-risk-limit", label="Total risk limit (%)",
                    value=float(risk_defaults["total_risk_percent"]), min=0.0001,
                    step=10 ** -int(risk_defaults["risk_decimals"]), suffix=" %",
                    debounce=INPUT_DEBOUNCE_MS,
                ),
            ]),
            status_region("multi-risk-summary"),
            dmc.Divider(label="Channel list"),
            html.Div(id="multi-channel-rows"),
            dmc.Button("Add channel", id="multi-add-channel", variant="light"),
            status_region("multi-channel-preview"),
            dmc.Divider(label="Shared parameters"),
            dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, spacing="md", children=[
                dmc.NumberInput(id="multi-balance-input", label="Starting balance (USD)", value=float(backtest_defaults["balance"]), min=1, step=1000, suffix=" USD", thousandSeparator=",", debounce=INPUT_DEBOUNCE_MS, **PERSIST),
                dmc.NumberInput(id="multi-leg-count-input", label="Leg count", value=int(defaults["LegCount"]), min=1, max=3, step=1),
                dmc.Select(id="multi-ksl-input", label="Shared kSL", data=options["strategies"][0]["ksl_options"], value=str(defaults["KslLevel"]), clearable=False),
                dmc.Select(id="multi-ktp-input", label="Leg 1 kTP", data=options["strategies"][0]["ktp_options"], value=str(defaults["KtpLevel"]), clearable=False),
                dmc.Select(id="multi-leg2-ktp-input", label="Leg 2 kTP", data=options["strategies"][0]["ktp_options"], value=str(defaults["Leg2Ktp"]), clearable=False),
                dmc.Select(id="multi-leg3-ktp-input", label="Leg 3 kTP", data=options["strategies"][0]["ktp_options"], value=str(defaults["Leg3Ktp"]), clearable=False),
            ]),
            dmc.Accordion(children=dmc.AccordionItem([
                dmc.AccordionControl("Position management limits"),
                dmc.AccordionPanel(dmc.SimpleGrid(cols={"base": 1, "md": 3}, children=[
                    dmc.Stack([dmc.Switch(id="multi-enable-daily-loss", label="Daily loss limit", checked=_bool_param(defaults["EnableDailyLossLimit"])), dmc.NumberInput(id="multi-max-daily-loss", label="Maximum", value=float(defaults["MaxDailyLossPercent"]), min=0.01, suffix=" %")]),
                    dmc.Stack([dmc.Switch(id="multi-enable-max-drawdown", label="Maximum drawdown", checked=_bool_param(defaults["EnableMaxDrawdown"])), dmc.NumberInput(id="multi-max-total-drawdown", label="Maximum", value=float(defaults["MaxTotalDrawdownPercent"]), min=0.01, suffix=" %")]),
                    dmc.Stack([dmc.Switch(id="multi-enable-consecutive-losses", label="Consecutive-loss limit", checked=_bool_param(defaults["EnableMaxConsecutiveLosses"])), dmc.NumberInput(id="multi-max-consecutive-losses", label="Number of trades", value=int(defaults["MaxConsecutiveLosses"]), min=1, step=1)]),
                ])),
            ], value="risk")),
            dmc.Divider(label="Period set"),
            dmc.Switch(id="multi-custom-period", label="Use a custom date range (1 period)", checked=False),
            dmc.Text("Default: from2022, 17 independent periods.", size="sm", c="dimmed"),
            status_region("multi-history-status"),
            dmc.SimpleGrid(cols={"base": 1, "sm": 2}, children=[
                dmc.DateInput(id="multi-start-input", label="Start date", value="2025-01-01", valueFormat="YYYY-MM-DD", maxDate=today, disabled=True),
                dmc.DateInput(id="multi-end-input", label="End date", value=today, valueFormat="YYYY-MM-DD", maxDate=today, disabled=True),
            ]),
            dmc.Group([dmc.Button("Create draft session", id="multi-create-btn", color="blue"), dcc.Loading(status_region("multi-create-message"), type="circle")]),
        ], gap="md")),
        dmc.Paper(withBorder=True, radius="md", p="md", mt="md", children=dmc.Stack([
            dmc.Group([dmc.Title("Saved multi-channel sessions", order=4), dmc.Button("Refresh", id="multi-refresh-btn", variant="light", size="xs")], justify="space-between"),
            dmc.Select(id="multi-session-select", label="Session", data=[], clearable=True, searchable=True),
            dmc.Group([
                dcc.ConfirmDialogProvider(id="multi-run-confirm", message="Run exactly the new periods and the CLI usage shown on the run card?", children=dmc.Button("Run selected session", id="multi-run-btn", color="orange")),
                dmc.Button("Stop", id="multi-stop-btn", color="red", disabled=True),
                dmc.Button("Delete session", id="multi-delete-btn", color="red", variant="outline", disabled=True),
            ]),
            dmc.Progress(id="multi-progress-bar", value=0, striped=True, animated=True),
            dmc.Text(id="multi-progress-label", children="No session selected", size="sm", c="dimmed"),
            status_region("multi-run-message"), status_region("multi-stop-message"), status_region("multi-delete-message"),
        ])),
        html.Div(id="multi-session-detail", style={"marginTop": "1rem"}),
    ])


def select_data(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"value": item["session_id"], "label": f"{item['title']} — {signal_sources.label_from_input((item.get('plan') or {}).get('input'))} — {item['state']}"} for item in sessions]


def channel_preview(bounds: dict[str, Any], _options: dict[str, Any]) -> Any:
    if not bounds.get("available"):
        return dmc.Alert(bounds.get("error") or "Invalid channel list", color="red")
    return dmc.Alert(f"{bounds['channel_count']} valid channels · Shared Redis: {bounds['earliest_date']} → {bounds['latest_date']}", color="blue", variant="light")


def _table(columns: list[dict[str, str]], rows: list[dict[str, Any]]) -> Any:
    return dash_table.DataTable(columns=columns, data=rows, page_action="native", page_size=20, sort_action="native", style_table={"overflowX": "auto"}, style_header={"fontWeight": 600, "backgroundColor": "#f8fafc"}, style_cell={"padding": "8px", "fontFamily": "system-ui", "fontSize": 13})


def contribution_detail(period: dict[str, Any] | None) -> Any:
    if not period:
        return dmc.Text("No period results to analyze yet.", c="dimmed")
    if period.get("contribution_available") is False:
        return dmc.Alert(
            "Trade-level contribution is not available for this period.",
            color="orange", variant="light",
        )
    rows = []
    for row in period.get("contributions") or []:
        rows.append({**row, "total_profit": round(float(row.get("total_profit") or 0), 2), "total_loss": round(-float(row.get("total_loss") or 0), 2), "net_contribution": round(float(row.get("net_contribution") or 0), 2), "profit_share_pct": round(float(row.get("profit_share_pct") or 0), 2), "loss_share_pct": round(float(row.get("loss_share_pct") or 0), 2)})
    labels = [f"{row['strategy']} · {row['symbol']} · {row['timeframe']}" for row in rows]
    figure = go.Figure()
    figure.add_bar(x=labels, y=[row["total_profit"] for row in rows], name="Total Profit", marker_color="#16a34a")
    figure.add_bar(x=labels, y=[row["total_loss"] for row in rows], name="Total Loss", marker_color="#dc2626")
    figure.update_layout(barmode="relative", height=360, margin=dict(l=30, r=20, t=30, b=90), yaxis_title="USD")
    columns = [{"id": key, "name": label} for key, label in (
        ("strategy", "Strategy"), ("symbol", "Symbol"), ("timeframe", "Timeframe"),
        ("risk_percent", "Risk %"), ("total_profit", "Total Profit"), ("total_loss", "Total Loss"),
        ("net_contribution", "Net Contribution"), ("profit_share_pct", "Profit Share %"),
        ("loss_share_pct", "Loss Share %"), ("wins", "Winning Trades"), ("losses", "Losing Trades"),
        ("trades", "Total Trades"), ("warning", "Warning"),
    )]
    warnings = [
        f"{row['strategy']} · {row['symbol']} · {row['timeframe']}: {row['warning']}"
        for row in rows if row.get("warning")
    ]
    warning_view = dmc.Alert(
        html.Ul([html.Li(message) for message in warnings]),
        title="Channels with no orders", color="orange", variant="light",
    ) if warnings else None
    return dmc.Stack([
        *([warning_view] if warning_view is not None else []),
        dcc.Graph(figure=figure, config={"displayModeBar": False}), _table(columns, rows),
    ])


def detail(session: dict[str, Any] | None, execution: dict[str, Any] | None, events: list[dict[str, Any]], _options: dict[str, Any]) -> Any:
    if not session:
        return dmc.Paper(withBorder=True, p="xl", children=dmc.Text("Create or select a session.", c="dimmed"))
    plan = session.get("plan") or {}
    if plan.get("schema") != actions.MULTI_CHANNEL_SCHEMA:
        if not (execution or {}).get("exists"):
            return dmc.Alert("The result has been deleted.", title="Legacy Multi-channel", color="orange")
        sections: list[Any] = [
            dmc.Alert("v1 sessions are view-only and cannot be run again.", color="orange")
        ]
        if (execution or {}).get("row"):
            sections.extend(single_views._execution_sections(
                execution or {}, str(session.get("experiment") or ""),
                activity_prefix="multi-session",
            ))
        return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections))
    input_doc = plan.get("input") or {}
    preflight = plan.get("preflight") or {}
    periods = list((execution or {}).get("periods") or [])
    def rounded(value: Any) -> float | None:
        return round(float(value), 2) if value is not None else None

    overview = [{
        "period": "custom" if input_doc.get("custom_period") else row.get("window"),
        "range": f"{row.get('start')} → {row.get('end')}", "net_profit": row.get("net_profit"),
        "max_dd": row.get("max_equity_drawdown_pct"), "total_profit": rounded(row.get("total_profit")),
        "total_loss": rounded(row.get("total_loss")), "total_trades": row.get("total_trades"),
        "margin": "*" if int(row.get("margin_rejections") or 0) > 0 else "",
        "source": "= db0" if row.get("fallback_db0") else row.get("result_source"), "status": row.get("status"),
    } for row in periods]
    plan_source = signal_sources.profile_from_input(input_doc)
    channels = [
        {**row, "timeframe": str(row.get("timeframe") or "").upper(),
         "source": signal_sources.LABELS.get(row.get("source") or plan_source) or signal_sources.LEGACY_LABELS.get(plan_source, "")}
        for row in input_doc.get("channels") or []
    ]
    risk_allocation = preflight.get("risk_allocation") or input_doc.get("risk_allocation") or {}
    risk_mode = str(risk_allocation.get("mode") or actions.MULTI_CHANNEL_RISK_MANUAL)
    risk_each = preflight.get("risk_per_channel_percent")
    cards = [
        ("Channels", len(channels)), ("Periods", preflight.get("period_count")),
        ("Total RunSpec", preflight.get("total_count")), ("Existing", preflight.get("existing_count")),
        ("New runs", preflight.get("new_count")),
        ("CLI today", f"{preflight.get('cli_used', 0)}/{preflight.get('cli_limit', 2000)}"),
        ("Engine", preflight.get("engine_profile")), ("Account", preflight.get("account_mode")),
        ("Total risk limit", f"{risk_allocation.get('total_percent')}%"),
        ("Risk allocation", "Equal split" if risk_mode == actions.MULTI_CHANNEL_RISK_EQUAL else "Manual"),
        ("Risk per channel", f"{risk_each}%" if risk_each is not None else "See channel table"),
        ("Total risk used", f"{preflight.get('risk_used_percent')}%"),
    ]
    sections: list[Any] = [
        dmc.Group([dmc.Title(str(session.get("title")), order=3), dmc.Badge(str(session.get("state", "")).upper())], justify="space-between"),
        dmc.Divider(label="Frozen run card"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, children=[dmc.Paper(withBorder=True, p="sm", children=[dmc.Text(k, size="xs", c="dimmed"), dmc.Text(str(v), fw=600)]) for k, v in cards]),
        dmc.Text(f"Source: {signal_sources.label_from_input(input_doc)} · Balance: {single_views._format_value(input_doc.get('balance'), 'money')} · max_parallel: {preflight.get('max_parallel')}", size="sm", c="dimmed"),
        _table([{"id": "strategy", "name": "Strategy"}, {"id": "symbol", "name": "Symbol"}, {"id": "timeframe", "name": "Timeframe"}, {"id": "source", "name": "Signal source"}, {"id": "risk_percent", "name": "Risk / leg group (%)"}], channels),
    ]
    if session.get("error_text"):
        sections.append(dmc.Alert(session["error_text"], color="red"))
    if events:
        event_rows = [{
            "time": event.get("at_utc"),
            "event": str(event.get("kind") or "unknown").replace("_", " ").title(),
            "detail": event.get("detail") or "--",
        } for event in events]
        sections.extend([
            dmc.Divider(label="Session lifecycle"),
            _table([
                {"id": "time", "name": "Time (UTC)"},
                {"id": "event", "name": "Event"},
                {"id": "detail", "name": "Detail"},
            ], event_rows),
        ])
    if (execution or {}).get("read_error"):
        sections.append(dmc.Alert(execution["read_error"], title="Could not read result", color="red"))
    elif not periods:
        text = "The result has been deleted." if session.get("state") in {"completed", "cached"} else "This session has no results yet."
        sections.append(dmc.Alert(text, color="orange"))
    else:
        if (execution or {}).get("read_warning"):
            sections.append(dmc.Alert(execution["read_warning"], color="yellow"))
        columns = [{"id": key, "name": label} for key, label in (("period", "Period"), ("range", "Date range"), ("net_profit", "Net Profit"), ("max_dd", "Max Equity DD %"), ("total_profit", "Total Profit"), ("total_loss", "Total Loss"), ("total_trades", "Total Trades"), ("margin", "Margin"), ("source", "Source"), ("status", "Status"))]
        sections.extend([
            dmc.Divider(label="Results by Period"),
            dmc.Text("Total Profit/Loss is the sum of each trade's net P&L; money is not added across periods. * = has margin rejection.", size="xs", c="dimmed"),
            _table(columns, overview),
            dmc.Select(id="multi-period-select", label="Period for channel contribution", data=[{"label": str(row["period"]), "value": str(i)} for i, row in enumerate(overview)], value="0", clearable=False),
            dcc.Store(id="multi-period-data", data=periods),
            html.Div(id="multi-period-contribution", children=contribution_detail(periods[0])),
        ])
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))
