"""Pure presentation for the independent Walk-forward dashboard session."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from dash import dash_table, dcc, html
import dash_mantine_components as dmc
try:
    from .. import signal_sources
except ImportError:  # app.py loads feature folders as top-level modules.
    import signal_sources


def layout(options: dict[str, Any]) -> html.Div:
    combo = next(item for item in options["strategies"] if item["value"] == "combo")
    default_symbol = next((x["value"] for x in options["symbols"] if x["value"] == "US30.cash"), options["symbols"][0]["value"])
    return html.Div([
        dcc.Interval(id="wf-progress-poll", interval=1_000, n_intervals=0, disabled=True),
        dcc.Store(id="wf-selection-request", data=None),
        dcc.Store(id="wf-run-state", data={"active": False}),
        html.Div(id="wf-live-progress", children=live_progress(None, None)),
        dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
            dmc.Group([
                dmc.Stack([
                    dmc.Title("Walk-forward Sessions", order=3),
                    dmc.Text("Independently test full parameter ranges across rolling in-sample and out-of-sample windows. Creating a draft never starts cTrader CLI.", c="dimmed"),
                ], gap="xs"),
                dmc.Badge("Research stage 2", color="violet", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Divider(label="Research target"),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.Select(id="wf-strategy-select", label="Strategy", data=[{"label": x["label"], "value": x["value"]} for x in options["strategies"]], value="combo", clearable=False),
                dmc.Select(id="wf-symbol-select", label="Symbol", data=options["symbols"], value=default_symbol, clearable=False, searchable=True),
                dmc.Select(id="wf-timeframe-select", label="Timeframe", data=[{"label": tf.upper(), "value": tf} for tf in combo["timeframes"]], value=combo["timeframes"][0], clearable=False),
            ]),
            html.Div(id="wf-history-status"),
            dmc.SimpleGrid(cols=2, spacing="md", children=[
                dmc.DateInput(id="wf-start-input", label="Start date", value="2025-01-01", valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=date.today().isoformat(), clearable=False, inputProps={"readOnly": True}),
                dmc.DateInput(id="wf-end-input", label="End date", value=date.today().isoformat(), valueFormat="YYYY-MM-DD", minDate="2000-01-01", maxDate=date.today().isoformat(), clearable=False, inputProps={"readOnly": True}),
            ]),
            dmc.Divider(label="Capital and execution"),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.NumberInput(id="wf-balance-input", label="Starting balance (USD)", value=100000, min=1, step=1000, suffix=" USD", thousandSeparator=","),
                dmc.NumberInput(id="wf-risk-input", label="Risk per trade", value=0.5, min=0.01, step=0.01, suffix=" %"),
                dmc.NumberInput(id="wf-parallel-input", label="Parallel CLI workers", value=10, min=1, max=16, step=1),
            ]),
            dmc.Divider(label="Rolling protocol"),
            dmc.Select(
                id="wf-window-preset", label="Window preset",
                data=[
                    {"value": "12:4", "label": "12 months in-sample / 4 months out-of-sample"},
                    {"value": "9:3", "label": "9 months in-sample / 3 months out-of-sample"},
                    {"value": "6:2", "label": "6 months in-sample / 2 months out-of-sample"},
                    {"value": "3:1", "label": "3 months in-sample / 1 month out-of-sample"},
                ], value="12:4", clearable=False,
            ),
            dmc.SimpleGrid(cols=3, spacing="md", children=[
                dmc.NumberInput(id="wf-is-months-input", label="In-sample months", value=12, min=1, max=60, step=1),
                dmc.NumberInput(id="wf-oos-months-input", label="Out-of-sample months", value=4, min=1, max=24, step=1),
                dmc.NumberInput(id="wf-step-months-input", label="Step months (matches OOS)", value=4, min=1, max=24, step=1, disabled=True),
            ]),
            dmc.MultiSelect(
                id="wf-objectives-select", label="Selection objectives (up to 3)",
                data=[
                    {"value": "net_profit", "label": "Net profit"},
                    {"value": "profit_factor", "label": "Profit factor"},
                    {"value": "win_rate", "label": "Win rate"},
                ], value=["net_profit"], maxValues=3, clearable=True,
                description="Each objective creates an independent Walk-forward draft and always evaluates both Plateau and Best.",
            ),
            dmc.SimpleGrid(cols=2, spacing="md", children=[
                dmc.Select(id="wf-primary-objective-select", label="Primary objective (choose before OOS)",
                           data=[{"value": "net_profit", "label": "Net profit"}, {"value": "profit_factor", "label": "Profit factor"}, {"value": "win_rate", "label": "Win rate"}],
                           value="net_profit", clearable=False),
                dmc.Select(id="wf-primary-rule-select", label="Primary selection rule (choose before OOS)",
                           data=[{"value": "plateau", "label": "Plateau"}, {"value": "best", "label": "Best"}],
                           value="plateau", clearable=False),
            ]),
            dmc.Alert("Protocol v1: full kSL and kTP Fibonacci ranges; minimum 30 trades; Plateau and Best selection rules. Step months always matches Out-of-sample months.", title="Frozen research protocol", color="blue", variant="light"),
            dmc.Alert("Each objective is a separate experiment. The primary objective and rule are frozen in the dashboard draft; other variants are exploratory. Win rate alone can select an unprofitable strategy.", title="Interpretation reminder", color="orange", variant="light"),
            html.Div(id="wf-commission-warning"),
            html.Div(id="wf-workload-summary"),
            dmc.Group([dmc.Button("Create Walk-forward draft (does not run)", id="wf-create-btn", color="blue"), html.Div(id="wf-create-message")], align="center"),
        ])),
        dmc.Paper(withBorder=True, radius="md", p="md", mt="md", children=dmc.Stack([
            dmc.Group([dmc.Title("Saved Walk-forward sessions", order=4), dmc.Button("Refresh sessions", id="wf-refresh-btn", variant="light", size="xs")], justify="space-between"),
            dmc.Select(id="wf-session-select", label="Select a Walk-forward session", data=[], placeholder="No Walk-forward sessions yet", clearable=True, searchable=True),
            dmc.Text("A Walk-forward session is independent of Grid Search. Completed or cached sessions can be opened without calling cTrader again.", size="xs", c="dimmed"),
            html.Div(id="wf-budget-confirmation", style={"display": "none"}, children=dmc.Checkbox(
                id="wf-budget-confirm", checked=False, color="orange",
            )),
            dmc.Group([
                dmc.Button("Run selected Walk-forward", id="wf-run-btn", color="orange"),
                dmc.Button("Stop active Walk-forward", id="wf-stop-btn", color="red", disabled=True),
                dmc.Button("Delete selected Walk-forward", id="wf-delete-btn", color="red", variant="outline", disabled=True),
            ]),
            html.Div(id="wf-run-message"), html.Div(id="wf-stop-message"),
            dmc.Text("Delete removes dashboard metadata only; core reports and pipeline caches are retained.", size="xs", c="dimmed"),
            html.Div(id="wf-delete-message"),
        ])),
        html.Div(id="wf-session-detail", style={"marginTop": "1rem"}),
    ])


def select_data(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"value": item["session_id"], "label": f"{item['title']} - {signal_sources.label_from_input((item.get('plan') or {}).get('input'))} - {item['state']} - {item['created_utc'][:19]}"} for item in sessions]


def live_progress(
    session: dict[str, Any] | None,
    progress: dict[str, Any] | None,
    *,
    starting: bool = False,
    selected_session_id: str | None = None,
) -> Any:
    """A compact, persistent view of the active run independent of the form."""
    if session is None:
        message = ("Starting the selected Walk-forward run; checking its frozen inputs and cTrader CLI."
                   if starting else "No Walk-forward run is active. Select a saved session below to inspect past results.")
        return dmc.Paper(withBorder=True, radius="md", p="md", mb="md", children=dmc.Stack([
            dmc.Group([dmc.Title("Live Walk-forward progress", order=4),
                       dmc.Badge("STARTING" if starting else "IDLE", color="yellow" if starting else "gray")], justify="space-between"),
            dmc.Text(message, size="sm", c="dimmed"),
        ], gap="xs"))

    inputs = (session.get("plan") or {}).get("input") or {}
    progress = progress or {}
    train = progress.get("train") or {}
    oos = progress.get("oos") or {}
    selection = progress.get("selection")
    train_counts = train.get("counts") or {}
    oos_counts = oos.get("counts") or {}
    train_total = int(inputs.get("train_backtests") or 0)
    train_ok = min(train_total, int(train_counts.get("ok") or 0))
    oos_total = (int(selection.get("selected") or 0) if selection is not None
                 else int(inputs.get("max_oos_backtests") or 0))
    oos_ok = min(oos_total, int(oos_counts.get("ok") or 0))
    train_percent = 100 * train_ok / train_total if train_total else 0
    oos_percent = 100 * oos_ok / oos_total if oos_total and selection is not None else 0
    active = int(train.get("running") or 0) + int(oos.get("running") or 0)
    unsuccessful = sum(int(count) for counts in (train_counts, oos_counts)
                       for status, count in counts.items() if status not in {"ok", "running"})
    configured = int(((session.get("plan") or {}).get("command_config") or {}).get("max_parallel") or 0)
    elapsed = _seconds_since(session.get("started_utc"))
    last_ok = _seconds_since(progress.get("last_ok_utc"))
    last_result = "No successful backtest yet" if last_ok is None else f"{_elapsed_text(last_ok)} ago"

    children: list[Any] = [
        dmc.Group([dmc.Title("Live Walk-forward progress", order=4), dmc.Badge("RUNNING", color="yellow")], justify="space-between"),
        dmc.Text(str(session.get("title") or "Walk-forward"), fw=600, size="sm"),
        dmc.Text(f"Signal source: {signal_sources.label_from_input(inputs)}", size="xs", c="dimmed"),
        dmc.SimpleGrid(cols=4, spacing="xs", children=[
            _stat("Active CLI workers", f"{active:,} / {configured:,}"),
            _stat("Unsuccessful runs", f"{unsuccessful:,}"),
            _stat("Elapsed", _elapsed_text(elapsed) if elapsed is not None else "—"),
            _stat("Last successful run", last_result),
        ]),
        dmc.Text((f"Latest success: {str(progress['last_ok_utc'])[:19]} UTC" if progress.get("last_ok_utc")
                  else "No completed backtest has been recorded yet."), size="xs", c="dimmed"),
        dmc.Stack([
            dmc.Group([dmc.Text("Train · full parameter grid", fw=600, size="sm"),
                       dmc.Text(f"{train_ok:,} / {train_total:,} successful ({train_percent:.1f}%) · {int(train.get('running') or 0):,} active",
                                size="sm", c="dimmed")], justify="space-between"),
            dmc.Progress(value=train_percent,
                         striped=bool(train.get("running")), animated=bool(train.get("running")), color="blue"),
        ], gap=4),
        dmc.Stack([
            dmc.Group([dmc.Text("Out-of-sample tests", fw=600, size="sm"),
                       dmc.Text((f"{oos_ok:,} / {oos_total:,} successful ({oos_percent:.1f}%) · {int(oos.get('running') or 0):,} active"
                                 if selection is not None else f"Waiting for Train · at most {oos_total:,} tests"),
                                size="sm", c="dimmed")], justify="space-between"),
            dmc.Progress(value=oos_percent,
                         striped=bool(oos.get("running")), animated=bool(oos.get("running")), color="teal"),
        ], gap=4),
        dmc.Text("Only this progress card updates automatically while a Walk-forward job is active; the results table does not reload.", size="xs", c="dimmed"),
    ]
    if unsuccessful:
        children.append(dmc.Alert(f"{unsuccessful:,} Train/OOS run(s) did not finish successfully. They are not counted in the bars above.",
                                  title="Execution needs review", color="orange", variant="light"))
    if selected_session_id and selected_session_id != session.get("session_id"):
        children.append(dmc.Text("Another saved session is selected below. Select this running session there to stop it.", size="xs", c="dimmed"))
    return dmc.Paper(withBorder=True, radius="md", p="md", mb="md", children=dmc.Stack(children, gap="sm"))


def _seconds_since(value: Any) -> float | None:
    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _elapsed_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s" if hours else f"{minutes}m {seconds:02d}s"


def workload_summary(*, windows: int, full_grid: int, objectives: int, workers: int, budget: dict[str, int], low_signal_windows: int = 0, signal_preflight_unavailable: bool = False) -> Any:
    train = objectives * windows * full_grid
    oos = objectives * windows * 2
    total = train + oos
    minutes = total * 38 / 100 * 10 / max(1, workers)
    color = "red" if total > budget["remaining"] else "orange" if total >= 500 else "blue"
    warning = " This exceeds today's remaining allowance; a retry after Prague midnight will be needed." if total > budget["remaining"] else ""
    if low_signal_windows:
        warning += f" {low_signal_windows} IS window(s) have fewer than 30 source signals; the 30-trade selection threshold may be unreachable."
    if signal_preflight_unavailable:
        warning += " Redis signal-count preflight is unavailable; verify signal coverage before running."
    return dmc.Alert(dmc.SimpleGrid(cols=5, spacing="xs", children=[
        _stat("Windows per objective", f"{windows:,}"), _stat("Train backtests across drafts", f"{train:,}"),
        _stat("Maximum OOS backtests", f"{oos:,}"), _stat("Rough time estimate", _duration(minutes * 60)),
        _stat("CLI budget today", f"{budget['used']:,} used / {budget['remaining']:,} remaining"),
    ]), title=f"{objectives} independent draft(s): up to {total:,} planned backtests.{warning} Time assumes the selected worker count; retries may add CLI launches.", color=color, variant="light")


def detail(session: dict[str, Any] | None, progress: dict[str, Any] | None, rows: list[dict[str, Any]], options: dict[str, Any]) -> Any:
    if not session:
        return dmc.Paper(withBorder=True, radius="md", p="xl", children=dmc.Text("Create or select a Walk-forward session to inspect its frozen protocol and results.", c="dimmed"))
    plan, inputs, preflight = session.get("plan") or {}, (session.get("plan") or {}).get("input") or {}, (session.get("plan") or {}).get("preflight") or {}
    state = str(session.get("state") or "unknown")
    color = {"draft":"gray", "running":"yellow", "completed":"green", "cached":"teal", "cancelled":"orange", "failed":"red", "inputs_changed":"red"}.get(state, "gray")
    cards = [
        _stat("Strategy", "MA Cross" if inputs.get("strategy") == "macross" else "Combo"),
        _stat("Symbol", str(inputs.get("symbol") or "—")), _stat("Timeframe", str(inputs.get("timeframe") or "").upper()),
        _stat("Signal source", signal_sources.label_from_input(inputs)),
        _stat("Period", f"{inputs.get('start','—')} to {inputs.get('end','—')}") , _stat("Starting balance", _usd(inputs.get("balance"))),
        _stat("Risk per trade", _percent(inputs.get("fixed_params", {}).get("RiskPercent"))), _stat("Rolling windows", f"{inputs.get('window_count', 0):,}"),
        _stat("Parameter range", f"Full range: {preflight.get('full_ksl_count', 0)} kSL × {preflight.get('full_ktp_count', 0)} kTP"),
        _stat("Protocol", f"30+ trades · {str(inputs.get('protocol', {}).get('objective') or '—').replace('_', ' ').title()} · Plateau + Best"),
    ]
    body: list[Any] = [
        dmc.Group([dmc.Stack([dmc.Title(str(session.get("title") or "Walk-forward"), order=3), dmc.Text(f"Session ID: {session.get('session_id')}", size="sm", c="dimmed")], gap="xs"), dmc.Badge(state.upper(), color=color, size="lg")], justify="space-between"),
        dmc.Divider(label="Frozen configuration"), dmc.SimpleGrid(cols=3, spacing="xs", children=cards),
    ]
    if session.get("error_text"):
        body.append(dmc.Alert(str(session["error_text"]), title="Walk-forward session error", color="red"))
    interpretation = plan.get("interpretation") or {}
    if interpretation:
        primary_objective = str(interpretation.get("primary_objective") or "").replace("_", " ").title()
        primary_rule = str(interpretation.get("primary_rule") or "").title()
        role = str(interpretation.get("role") or "exploratory")
        role_message = {
            "primary": "This session contains the pre-registered primary objective.",
            "exploratory": "This objective is exploratory; do not promote it after inspecting OOS.",
            "retrospective": "OOS data already existed when this draft was created; this choice is retrospective, not pre-registered.",
        }.get(role, "Interpret this comparison as exploratory.")
        body.append(dmc.Alert(
            f"Planned primary comparison: {primary_objective} + {primary_rule}. {role_message}",
            title="Interpretation status", color="blue" if role == "primary" else "orange", variant="light",
        ))
    result = session.get("result") or {}
    without_pick = int(result.get("windows_without_a_pick") or 0)
    if without_pick:
        body.append(dmc.Alert(f"{without_pick:,} window-rule decision(s) had no eligible IS parameter pick. Their OOS test did not run.", title="Missing selections", color="orange", variant="light"))
    if result.get("phase_stopped"):
        body.append(dmc.Text(f"Last execution stopped during {str(result['phase_stopped']).upper()}.", size="sm", c="dimmed"))
    if progress and progress.get("exists"):
        body.extend([dmc.Divider(label="Execution progress"), _phase_progress(progress, inputs)])
        unsuccessful = sum(int(value) for phase in ("train", "oos")
                           for status, value in ((progress.get(phase) or {}).get("counts") or {}).items()
                           if status not in {"ok", "running"})
        if unsuccessful:
            body.append(dmc.Alert(f"{unsuccessful:,} persisted Train/OOS run(s) are not successful. A completed-looking result may still need review of these runs.", title="Execution quality warning", color="orange", variant="light"))
    if rows:
        objective = str(inputs.get("protocol", {}).get("objective") or "net_profit")
        affected = sum(1 for row in rows if row.get("test_status") == "ok" and not row.get("test_strict_eligible"))
        if affected:
            body.append(dmc.Alert(f"{affected:,} completed OOS execution(s) have quality flags and are shown in the table but excluded from research-valid summaries.", title="OOS quality warning", color="orange", variant="light"))
        body.extend([dmc.Divider(label="Out-of-sample results"), _result_summary(rows, interpretation.get("primary_rule") if interpretation.get("role") == "primary" else None), _result_table(rows, options, objective)])
    elif state == "running":
        body.append(dmc.Alert("The session is running. The progress area above reads the persisted Train and OOS run states; use Refresh sessions to load finished window results.", title="Walk-forward in progress", color="yellow", variant="light"))
    else:
        body.append(dmc.Text("No out-of-sample result is available yet.", c="dimmed"))
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(body, gap="md"))


def _phase_progress(progress: dict[str, Any], inputs: dict[str, Any]) -> Any:
    train = progress.get("train") or {}; oos = progress.get("oos") or {}; selection = progress.get("selection")
    train_counts = train.get("counts") or {}; oos_counts = oos.get("counts") or {}
    train_total = int(inputs.get("train_backtests") or 0)
    train_done = min(train_total, int(train_counts.get("ok") or 0))
    oos_total = int(selection.get("selected") or 0) if selection else int(inputs.get("max_oos_backtests") or 0)
    oos_done = min(oos_total, int(oos_counts.get("ok") or 0))
    train_failed = sum(int(value) for status, value in train_counts.items() if status not in {"ok", "running"})
    oos_failed = sum(int(value) for status, value in oos_counts.items() if status not in {"ok", "running"})
    return dmc.Stack([
        dmc.Text(f"Train: {train_done:,} / {train_total:,} successful · {train_failed:,} unsuccessful · {int(train.get('running') or 0):,} active", size="sm"),
        dmc.Progress(value=(100 * train_done / train_total) if train_total else 0, color="blue", striped=bool(train.get("running")), animated=bool(train.get("running"))),
        dmc.Text((f"OOS: {oos_done:,} / {oos_total:,} successful · {oos_failed:,} unsuccessful · {int(oos.get('running') or 0):,} active" if selection else f"OOS: selection begins after Train completes (maximum {oos_total:,} backtests)"), size="sm"),
        dmc.Progress(value=(100 * oos_done / oos_total) if oos_total and selection else 0, color="teal", striped=bool(oos.get("running")), animated=bool(oos.get("running"))),
    ], gap="xs")


def _result_summary(rows: list[dict[str, Any]], primary_rule: str | None = None) -> Any:
    completed = [r for r in rows if r.get("test_status") == "ok"]
    valid = [r for r in rows if r.get("test_strict_eligible")]
    margin = [r for r in completed if r.get("test_quality") == "Margin rejection"]
    overview = dmc.SimpleGrid(cols=4, spacing="xs", children=[
        _stat("Window-rule decisions", f"{len(rows):,}"), _stat("Completed OOS executions", f"{len(completed):,}"),
        _stat("Research-valid OOS", f"{len(valid):,}"), _stat("OOS with margin rejection", f"{len(margin):,}"),
    ])
    by_rule = []
    for rule in ("plateau", "best"):
        selected = [r for r in valid if r.get("rule") == rule]
        profitable = sum(float(r.get("test_net_profit") or 0) > 0 for r in selected)
        total_profit = sum(float(r.get("test_net_profit") or 0) for r in selected)
        by_rule.append(dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([
            dmc.Text(rule.title() + (" · PRIMARY" if rule == primary_rule else " · EXPLORATORY" if primary_rule else ""), fw=700),
            dmc.Text(f"Research-valid windows: {len(selected):,} · Profitable: {profitable:,}", size="sm"),
            dmc.Text(f"Sum of independent OOS net profit: {_usd(total_profit)}", size="sm"),
            dmc.Text(f"Median window net profit: {_usd(_median([r.get('test_net_profit') for r in selected]))}", size="sm"),
        ], gap=4)))
    return dmc.Stack([overview, dmc.SimpleGrid(cols=2, spacing="xs", children=by_rule),
                      dmc.Text("Each OOS window starts from the configured balance; the sum is not a compounded account-equity curve. Margin-rejection executions remain visible below but are excluded from research-valid summaries.", size="xs", c="dimmed")], gap="sm")


def _result_table(rows: list[dict[str, Any]], options: dict[str, Any], objective: str) -> Any:
    data = []
    objective_label = objective.replace("_", " ").title()
    for row in rows:
        params = row.get("selected_params") or {}
        data.append({
            "Window": str(row.get("test_window") or "—"), "Rule": str(row.get("rule") or "—").title(),
            "Selected kSL": _fib(params.get("KslLevel"), options, "ksl_options"), "Selected kTP": _fib(params.get("KtpLevel"), options, "ktp_options"),
            **({f"IS {objective_label}": _objective_value(row.get(f"train_{objective}"), objective)} if objective != "net_profit" else {}),
            "Plateau score": _objective_value(row.get("plateau_score"), objective) if row.get("rule") == "plateau" else "—",
            "IS net profit": _usd(row.get("train_net_profit")), "IS trades": _int(row.get("train_total_trades")),
            "OOS status": str(row.get("test_status") or "Not run").upper(),
            "OOS quality": str(row.get("test_quality") or "Unverified"),
            "Quality details": ", ".join(str(reason).replace("_", " ") for reason in row.get("test_quality_reasons") or []) or "—",
            "OOS net profit": _usd(row.get("test_net_profit")),
            "OOS profit factor": _number(row.get("test_profit_factor")), "OOS trades": _int(row.get("test_total_trades")),
            "OOS max drawdown": _percent(row.get("test_max_equity_drawdown_pct")),
        })
    columns = [{"name": key, "id": key} for key in data[0]]
    return dash_table.DataTable(data=data, columns=columns, page_size=12, sort_action="native", style_table={"overflowX":"auto"}, style_cell={"fontFamily":"Arial", "fontSize":13, "padding":"8px", "textAlign":"left", "whiteSpace":"normal"}, style_header={"fontWeight":"bold", "backgroundColor":"#f7f9fc"})


def _objective_value(value: Any, objective: str) -> str:
    if objective == "net_profit":
        return _usd(value)
    if objective == "win_rate":
        try:
            return _percent(Decimal(str(value)) * 100)
        except Exception:
            return "—"
    return _number(value)


def _stat(label: str, value: str) -> Any:
    return dmc.Paper(withBorder=True, radius="sm", p="sm", children=dmc.Stack([dmc.Text(label, size="xs", c="dimmed"), dmc.Text(value, fw=700, size="sm")], gap=2))


def _fib(value: Any, options: dict[str, Any], key: str) -> str:
    if value is None: return "—"
    for strategy in options.get("strategies", []):
        for item in strategy.get(key, []):
            if str(item.get("value")) == str(value): return str(item.get("label"))
    return str(value)


def _usd(value: Any) -> str:
    try: return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f} USD"
    except Exception: return "—"


def _percent(value: Any) -> str:
    try: return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f}%"
    except Exception: return "—"


def _number(value: Any) -> str:
    try: return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f}"
    except Exception: return "—"


def _int(value: Any) -> str:
    try: return f"{int(value):,}"
    except Exception: return "—"


def _median(values: list[Any]) -> float | None:
    parsed = sorted(float(x) for x in values if x is not None)
    if not parsed: return None
    mid = len(parsed) // 2
    return parsed[mid] if len(parsed) % 2 else (parsed[mid - 1] + parsed[mid]) / 2


def _duration(seconds: float) -> str:
    minutes = max(0, int(seconds)) // 60
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"
