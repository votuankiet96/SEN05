"""Pure presentation for Grid DSR + PBO diagnostics."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from dash import dcc, html
import dash_mantine_components as dmc
try:
    from .. import signal_sources
except ImportError:  # app.py loads feature folders as top-level modules.
    import signal_sources


def layout() -> html.Div:
    return html.Div([
        dcc.Interval(
            id="diagnostics-progress-poll", interval=1_000, n_intervals=0, disabled=True,
        ),
        dcc.Store(id="diagnostics-selection-request", data=None),
        dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack([
            dmc.Group([
                dmc.Stack([
                    dmc.Title("Grid Diagnostics: DSR + PBO", order=3),
                    dmc.Text(
                        "Measure multiple-testing inflation and selection overfitting from a completed full Grid. "
                        "This analysis reads saved reports and never calls cTrader CLI.",
                        c="dimmed",
                    ),
                ], gap="xs"),
                dmc.Badge("Research stage 3", color="grape", variant="light"),
            ], justify="space-between", align="flex-start"),
            dmc.Group([
                dmc.Select(
                    id="diagnostics-source-select",
                    label="Source Grid session",
                    data=[],
                    placeholder="Select a completed full Grid",
                    searchable=True,
                    clearable=True,
                    style={"flex": 1},
                ),
                dmc.Button(
                    "Refresh sources", id="diagnostics-source-refresh-btn",
                    variant="light", size="xs", mt=24,
                ),
            ], align="flex-end"),
            dmc.SimpleGrid(cols={"base": 1, "sm": 2}, children=[
                dmc.NumberInput(
                    id="diagnostics-min-trades-input",
                    label="Minimum trades per trial",
                    value=0,
                    min=0,
                    step=1,
                    allowDecimal=False,
                ),
                dmc.NumberInput(
                    id="diagnostics-blocks-input",
                    label="PBO time blocks",
                    value=16,
                    disabled=True,
                    allowDecimal=False,
                ),
            ]),
            html.Div(id="diagnostics-source-status"),
            dmc.Group([
                dmc.Button(
                    "Create diagnostics draft (does not run)",
                    id="diagnostics-create-btn",
                    color="blue",
                ),
                html.Div(id="diagnostics-create-message"),
            ], align="center"),
        ])),
        dmc.Paper(withBorder=True, radius="md", p="md", mt="md", children=dmc.Stack([
            dmc.Group([
                dmc.Title("Saved diagnostics sessions", order=4),
                dmc.Button(
                    "Refresh sessions", id="diagnostics-refresh-btn",
                    variant="light", size="xs",
                ),
            ], justify="space-between"),
            dmc.Select(
                id="diagnostics-session-select",
                label="Select a diagnostics session",
                data=[],
                placeholder="No diagnostics sessions yet",
                searchable=True,
                clearable=True,
            ),
            dmc.Group([
                dmc.Button("Run selected diagnostics", id="diagnostics-run-btn", color="orange"),
                dmc.Button(
                    "Stop active analysis", id="diagnostics-stop-btn",
                    color="red", disabled=True,
                ),
                dmc.Button(
                    "Delete selected diagnostics", id="diagnostics-delete-btn",
                    color="red", variant="outline", disabled=True,
                ),
            ]),
            dmc.Progress(
                id="diagnostics-progress-bar", value=0, striped=True, animated=True,
            ),
            dmc.Text(
                id="diagnostics-progress-label",
                children="No diagnostics session selected",
                size="sm", c="dimmed",
            ),
            html.Div(id="diagnostics-run-message"),
            html.Div(id="diagnostics-stop-message"),
            html.Div(id="diagnostics-delete-message"),
        ])),
        html.Div(id="diagnostics-session-detail", style={"marginTop": "1rem"}),
    ])


def source_options(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "value": session["session_id"],
            "label": f"{session['title']} - {signal_sources.label_from_input((session.get('plan') or {}).get('input'))} - {session['state']}",
        }
        for session in sessions
        if session.get("state") in {"completed", "cached"}
    ]


def session_options(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "value": session["session_id"],
            "label": f"{session['title']} - {_diagnostic_source_label(session)} - {session['state']} - {session['created_utc'][:19]}",
        }
        for session in sessions
    ]


def _diagnostic_source_label(session: dict[str, Any]) -> str:
    frozen = ((session.get("plan") or {}).get("input") or {})
    return signal_sources.label_from_input({"redis_profile": frozen.get("source_redis_profile")})


def source_status(
    session: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    min_trades: int,
    policy_supported: bool,
) -> Any:
    if not session:
        return dmc.Alert(
            "Select a completed Grid to verify its lineage and completed execution trials.",
            color="blue", variant="light",
        )
    snapshot = snapshot or {}
    source_input = (session.get("plan") or {}).get("input") or {}
    expected = int(source_input.get("pass_count") or 0)
    rows = int(snapshot.get("row_count") or 0)
    completed_execution = int(snapshot.get("completed_execution_count") or 0)
    strict_eligible = int(snapshot.get("eligible_count") or 0)
    margin_trials = int(snapshot.get("margin_rejection_trial_count") or 0)
    margin_events = int(snapshot.get("margin_rejection_event_count") or 0)
    parameter_space = source_input.get("parameter_space") or {}
    ksl_count = len(parameter_space.get("KslLevel") or [])
    ktp_count = len(parameter_space.get("KtpLevel") or [])
    source_complete = (
        session.get("state") in {"completed", "cached"}
        and rows == expected
        and completed_execution == expected
    )
    ready = source_complete and policy_supported
    reasons = snapshot.get("ineligible_reasons") or {}
    reason_text = "; ".join(
        f"{count:,} {reason.replace('_', ' ')}" for reason, count in sorted(reasons.items())
    )
    return dmc.Alert(
        dmc.Stack([
            dmc.SimpleGrid(cols={"base": 2, "sm": 3, "lg": 6}, children=[
                _metric("Grid state", str(session.get("state") or "-").upper()),
                _metric("Parameter grid", f"{ksl_count} × {ktp_count}"),
                _metric("Expected trials", f"{expected:,}"),
                _metric("Stored rows", f"{rows:,}"),
                _metric("Completed executions", f"{completed_execution:,}"),
                _metric("Strict filter eligible", f"{strict_eligible:,}"),
            ]),
            dmc.Text(
                (
                    f"{margin_trials:,} completed trial(s) contain {margin_events:,} margin-rejection event(s). "
                    "They remain part of the completed-execution cohort."
                    if margin_trials else
                    "No completed trial contains a margin-rejection event."
                ),
                size="sm",
            ),
            dmc.Text(
                f"Strict filter exclusions: {reason_text}" if reason_text else
                f"All trials pass the strict filter at minimum trades {min_trades:,}.",
                size="sm", c="dimmed",
            ),
        ], gap="sm"),
        title=(
            "Source is ready for completed-execution diagnostics" if ready else
            "Source is complete; waiting for the core completed-execution policy" if source_complete else
            "Source does not meet the completed-execution contract"
        ),
        color="green" if ready else "yellow" if source_complete else "red",
        variant="light",
    )


def detail(session: dict[str, Any] | None) -> Any:
    if not session:
        return dmc.Paper(withBorder=True, radius="md", p="xl", children=dmc.Text(
            "Create or select a diagnostics session to view its frozen source and results.",
            c="dimmed",
        ))
    plan = session.get("plan") or {}
    frozen = plan.get("input") or {}
    result = session.get("result") or {}
    state = str(session.get("state") or "unknown")
    color = {
        "draft": "gray", "running": "yellow", "completed": "green",
        "cached": "teal", "cancelled": "orange", "failed": "red",
    }.get(state, "gray")
    sections: list[Any] = [
        dmc.Group([
            dmc.Stack([
                dmc.Title("DSR + PBO diagnostics", order=3),
                dmc.Text(_source_summary(session.get("title")), size="sm", c="dimmed"),
            ], gap="xs"),
            dmc.Badge(state.upper(), color=color, size="lg"),
        ], justify="space-between", align="flex-start"),
    ]
    if state == "running":
        sections.append(dmc.Alert(
            dmc.Stack([
                dmc.Text("PBO analysis is running", fw=700),
                dmc.Text(
                    str(session.get("progress_text") or "Preparing analysis"), size="sm",
                ),
                dmc.Text(
                    "This reads the saved Grid reports only. cTrader CLI is not running.",
                    size="xs", c="dimmed",
                ),
            ], gap=3),
            title="Analysis status", color="yellow", variant="light",
        ))
    sections.extend([
        dmc.Divider(label="Analysis inputs"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, children=[
            _metric(
                "Source signal",
                signal_sources.label_from_input({"redis_profile": frozen.get("source_redis_profile")}),
                "Frozen by the completed source Grid",
            ),
            _metric(
                "Completed Grid trials",
                f"{int(frozen.get('completed_execution_trials') or 0):,}",
                "Every combination that completed execution",
            ),
            _metric(
                "Starting balance",
                f"{_money(frozen.get('initial_balance'))} USD",
                "The balance used by the source Grid",
            ),
            _metric(
                "Results included",
                _trial_policy_label(frozen.get("trial_policy")),
                "Completed trials remain valid even if an order was rejected for margin",
            ),
            _metric(
                "Margin-rejection trials",
                f"{int(frozen.get('margin_rejection_trials') or 0):,}",
                "Completed trials containing one or more rejected orders",
            ),
            _metric(
                "PBO time blocks",
                f"{int(frozen.get('blocks') or 0):,} chronological blocks",
                "The automatic train/test splits used by PBO",
            ),
            _metric("Return measure", _return_field_label(frozen.get("field"))),
            _metric("Analysis timezone", frozen.get("timezone", "-")),
            _metric(
                "Minimum trades per trial",
                _minimum_trades_label(frozen.get("min_trades")),
            ),
        ]),
    ])
    if session.get("error_text"):
        sections.append(dmc.Alert(str(session["error_text"]), title="Diagnostics error", color="red"))
    if result.get("dsr") and result.get("pbo"):
        sections.extend(_results(result["dsr"], result["pbo"]))
    elif state == "draft":
        sections.append(dmc.Alert(
            "Inputs are frozen. Running this session performs Python analysis only; cTrader CLI is not used.",
            title="Ready to analyse", color="blue", variant="light",
        ))
    return dmc.Paper(withBorder=True, radius="md", p="md", children=dmc.Stack(sections, gap="md"))


def _results(dsr: dict[str, Any], pbo: dict[str, Any]) -> list[Any]:
    n_trials = int(dsr.get("n_trials") or pbo.get("n_trials") or 0)
    included_trials = int(dsr.get("included_trials") or n_trials)
    total_rows = int(dsr.get("total_rows") or included_trials)
    excluded_trials = int(dsr.get("excluded_trials") or 0)
    margin_trials = int(dsr.get("margin_rejection_trials") or 0)
    margin_events = int(dsr.get("margin_rejection_events") or 0)
    policy = str(dsr.get("trial_policy") or "-")
    dsr_probability = float(dsr.get("dsr") or 0)
    pbo_probability = float(pbo.get("pbo") or 0)
    items: list[Any] = [
        dmc.Divider(label="Research conclusion"),
        _conclusion(dsr_probability, pbo_probability, pbo),
        dmc.Text("Read these first", fw=700, size="sm"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, children=[
            _metric(
                "1. Overfitting probability (PBO)", _percent(pbo_probability),
                "How often the training winner fell into the lower half on its held-out period.",
            ),
            _metric(
                "2. Deflated Sharpe confidence", _percent(dsr_probability),
                "Confidence in the best Sharpe after allowing for the full parameter search.",
            ),
            _metric(
                "3. Trials analysed", f"{included_trials:,} / {total_rows:,}",
                "Completed parameter combinations included in both diagnostics.",
            ),
            _metric(
                "4. Daily observations", f"{int(dsr.get('sample_length') or 0):,}",
                "End-of-day account-balance observations per trial.",
            ),
        ]),
        dmc.Divider(label="What the diagnostics read"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, children=[
            _metric(
                "Report source", f"{included_trials:,} saved Grid reports",
                "Completed cTrader executions; no additional backtest was run.",
            ),
            _metric("Account series", "End-of-day account balance", "Europe/Prague calendar timezone."),
            _metric(
                "PBO validation", f"{int(pbo.get('splits_tested') or 0):,} chronological splits",
                f"All symmetric train/test splits from {int(pbo.get('blocks') or 0):,} time blocks.",
            ),
            _metric("Cohort rule", _trial_policy_label(policy)),
        ]),
        dmc.Divider(label="Audit details"),
        dmc.SimpleGrid(cols={"base": 1, "sm": 2, "lg": 4}, children=[
            _metric("Excluded trials", f"{excluded_trials:,}"),
            _metric("Margin-rejection trials", f"{margin_trials:,} trials / {margin_events:,} orders"),
            _metric("Winning candidate", _winner_reference(dsr.get("winner_label"))),
            _metric("PBO mean logit", _number(pbo.get("logit_mean"))),
        ]),
    ]
    if n_trials < 30:
        items.append(dmc.Alert(
            f"Only {n_trials:,} trials were analysed. Multiple-testing diagnostics are less informative for a small search.",
            title="Small trial set", color="orange", variant="light",
        ))
    elif margin_trials:
        items.append(dmc.Alert(
            f"{margin_trials:,} completed trial(s) included margin-rejected orders "
            f"({margin_events:,} event(s)). They were included under {policy}.",
            title="Actual account-execution result", color="blue", variant="light",
        ))
    return items


def _conclusion(dsr_probability: float, pbo_probability: float, pbo: dict[str, Any]) -> Any:
    """Explain the two diagnostics before exposing their implementation metrics."""
    split_count = int(pbo.get("splits_tested") or 0)
    pbo_text = (
        f"Across {split_count:,} chronological train/test splits, the parameter combination chosen "
        f"on the training portion landed in the lower half of the held-out portion { _percent(pbo_probability) } "
        "of the time."
    )
    dsr_text = (
        f"After correcting for the full parameter search, the selected winner has "
        f"{_percent(dsr_probability)} confidence of exceeding the multiple-testing benchmark."
    )
    if pbo_probability > 0.5:
        headline = "Caution: the selected winner deteriorated in a majority of held-out splits"
        color = "orange"
    else:
        headline = "The selected winner did not deteriorate in a majority of held-out splits"
        color = "blue"
    return dmc.Alert(
        dmc.Stack([
            dmc.Text(headline, fw=700),
            dmc.Text(pbo_text, size="sm"),
            dmc.Text(dsr_text, size="sm"),
            dmc.Text(
                "These diagnostics assess research reliability; they do not choose a parameter set or issue a trading decision.",
                size="xs", c="dimmed",
            ),
        ], gap=4),
        title="What this result means", color=color, variant="light",
    )


def _winner_reference(value: Any) -> str:
    """Keep an opaque core label out of the primary findings, but retain traceability."""
    text = str(value or "-")
    if text == "-":
        return text
    parts = text.split("__")
    if len(parts) >= 3:
        return " · ".join(part.upper() if part in {"h1", "h3", "m15"} else part for part in parts[:3])
    return text


def _metric(label: str, value: Any, hint: str | None = None) -> Any:
    content: list[Any] = [
        dmc.Text(label, size="xs", c="dimmed"),
        dmc.Text(str(value), fw=650, size="sm", style={"wordBreak": "break-word"}),
    ]
    if hint:
        content.append(dmc.Text(hint, size="xs", c="dimmed", lh=1.3))
    return dmc.Paper(
        withBorder=True, radius="sm", p="sm",
        children=dmc.Stack(content, gap=2),
    )


def _source_summary(title: Any) -> str:
    """Show the source Grid identity without exposing dashboard storage IDs."""
    source = str(title or "").removeprefix("DSR + PBO - ").strip()
    if not source:
        return "Saved Grid research source"
    parts = [part.strip() for part in source.split(" - ") if part.strip()]
    # Dashboard-generated titles always start with symbol, strategy, timeframe,
    # and combination count. Date details are visible in the source Grid itself.
    if len(parts) >= 4:
        return f"Source Grid: {' · '.join(parts[:4])}"
    return f"Source Grid: {source}"


def _trial_policy_label(value: Any) -> str:
    if str(value) == "completed_execution_v1":
        return "Completed execution results"
    if str(value) == "strict_research_v1":
        return "Strict research filter"
    return str(value or "-")


def _return_field_label(value: Any) -> str:
    if str(value) == "balance":
        return "End-of-day account balance"
    return str(value or "-")


def _minimum_trades_label(value: Any) -> str:
    count = int(value or 0)
    return "No minimum" if count == 0 else f"{count:,} trades"


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _number(value: Any) -> str:
    return f"{_decimal(value).quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f}"


def _money(value: Any) -> str:
    return _number(value)


def _percent(value: Any) -> str:
    return f"{(_decimal(value) * 100).quantize(Decimal('0.01'), rounding=ROUND_DOWN):,.2f}%"
