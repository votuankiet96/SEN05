"""Callback orchestration for the isolated Single Backtest dashboard tab."""
from __future__ import annotations

import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

_DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
_BO_WORKFLOW_ROOT = _DASHBOARD_ROOT.parent
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

from dash import Input, Output, State, callback, ctx, no_update
import dash_mantine_components as dmc

import actions
import data_access
import session_store
from ui_common import poll_disabled
from . import views as single_views

FORM_OPTIONS = data_access.session_form_options()


def _strategy_option(strategy: str) -> dict[str, Any]:
    for item in FORM_OPTIONS["strategies"]:
        if item["value"] == strategy:
            return item
    raise ValueError(f"Strategy is not available in the dashboard form: {strategy!r}")


def _selected_session_ids(value: str | list[str] | None) -> list[str]:
    """Normalise the Dash select value and keep the user's queue order."""
    raw_values = [value] if isinstance(value, str) else list(value or [])
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if raw is None:
            continue
        session_id = str(raw).strip()
        if session_id and session_id not in seen:
            seen.add(session_id)
            result.append(session_id)
    return result


def _selected_sessions(value: str | list[str] | None) -> list[dict[str, Any]]:
    """One dashboard-store query for all selected session metadata."""
    return session_store.get_many(_selected_session_ids(value))


@callback(
    Output("session-timeframe-select", "data"),
    Output("session-timeframe-select", "value"),
    Output("session-ksl-input", "data"),
    Output("session-ksl-input", "value"),
    Output("session-ktp-input", "data"),
    Output("session-ktp-input", "value"),
    Input("session-strategy-select", "value"),
)
def update_strategy_fields(strategy: str):
    option = _strategy_option(strategy)
    timeframes = option["timeframes"]
    return (
        [{"label": item.upper(), "value": item} for item in timeframes],
        [timeframes[0]],
        option["ksl_options"],
        option["default_ksl"],
        option["ktp_options"],
        option["default_ktp"],
    )


@callback(
    Output("session-start-input", "minDate"),
    Output("session-start-input", "maxDate"),
    Output("session-start-input", "value"),
    Output("session-end-input", "minDate"),
    Output("session-end-input", "maxDate"),
    Output("session-end-input", "value"),
    Output("session-history-status", "children"),
    Input("session-symbol-select", "value"),
    Input("session-timeframe-select", "value"),
    Input("session-strategy-select", "value"),
    Input("global-signal-profile", "value"),
)
def update_signal_date_limits(symbols: list[str] | None, timeframes: list[str] | None, strategy: str, redis_profile: str):
    today = date.today()
    bounds = data_access.signal_date_bounds_for_selection(
        symbols or [], timeframes or [], strategy or "", redis_profile=redis_profile,
    )
    if not bounds.get("available"):
        return (
            "2000-01-01", today.isoformat(), None,
            "2000-01-01", today.isoformat(), None,
            dmc.Alert(bounds["error"], title="Signal history unavailable", color="red", variant="light"),
        )
    earliest = date.fromisoformat(bounds["earliest_date"])
    default_start = max(earliest, date(2025, 1, 1))
    if default_start >= today:
        return (
            earliest.isoformat(), today.isoformat(), None,
            earliest.isoformat(), today.isoformat(), None,
            dmc.Alert(
                "Signal history does not contain a complete selectable date range.",
                title="Signal history unavailable", color="red", variant="light",
            ),
        )
    return (
        earliest.isoformat(), today.isoformat(), default_start.isoformat(),
        earliest.isoformat(), today.isoformat(), today.isoformat(),
        dmc.Text(
            f"Redis signal history across {bounds['pair_count']} selected combination"
            f"{'s' if bounds['pair_count'] != 1 else ''}: common coverage "
            f"{bounds['earliest_date']} to {bounds['latest_date']}. "
            "Start date cannot be earlier than the common signal-history cutoff.",
            size="sm", c="dimmed",
        ),
    )


@callback(
    Output("session-selection-request", "data"),
    Output("session-create-message", "children"),
    Input("create-session-btn", "n_clicks"),
    State("session-symbol-select", "value"),
    State("session-timeframe-select", "value"),
    State("session-strategy-select", "value"),
    State("session-start-input", "value"),
    State("session-end-input", "value"),
    State("session-balance-input", "value"),
    State("session-risk-input", "value"),
    State("session-ksl-input", "value"),
    State("session-ktp-input", "value"),
    State("global-signal-profile", "value"),
    prevent_initial_call=True,
)
def create_session(
    _clicks: int,
    symbols: list[str] | None,
    timeframes: list[str] | None,
    strategy: str,
    start: str,
    end: str,
    balance: float,
    risk_percent: float,
    ksl_level: int,
    ktp_level: int,
    redis_profile: str,
):
    try:
        selected_profile = session_store.confirmed_signal_profile(redis_profile)
        bounds = data_access.signal_date_bounds_for_selection(
            symbols or [], timeframes or [], strategy or "", redis_profile=selected_profile,
        )
        if not bounds.get("available"):
            raise ValueError(bounds["error"])
        if not start or not end:
            raise ValueError("Select both a start date and an end date.")
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        earliest_date = date.fromisoformat(bounds["earliest_date"])
        if start_date < earliest_date:
            raise ValueError(
                f"Start date cannot be earlier than the Redis signal-history cutoff: {earliest_date.isoformat()}."
            )
        if end_date > date.today():
            raise ValueError("End date cannot be later than today.")
        # Prepare and validate every independent one-process plan before
        # writing anything to dashboard state.  No core command is invoked.
        plans = [
            actions.prepare_single_backtest_session(
                symbol=pair["symbol"],
                timeframe=pair["timeframe"],
                strategy=strategy or "",
                start=start,
                end=end,
                balance=float(balance),
                risk_percent=float(risk_percent),
                ksl_level=ksl_level,
                ktp_level=ktp_level,
                redis_profile=selected_profile,
            )
            for pair in bounds["pairs"]
        ]
        sessions = session_store.create_draft_group(plans)
        count = len(sessions)
        message = dmc.Alert(
            f"Created {count} draft{'s' if count != 1 else ''} as one dashboard group. "
            "No draft has called cTrader CLI.",
            title="Session inputs frozen", color="green", variant="light",
        )
        return {"ids": [session["session_id"] for session in sessions]}, message
    except Exception as exc:
        return no_update, dmc.Alert(str(exc), title="Could not create session", color="red")


@callback(
    Output("session-select", "data"),
    Output("session-select", "value"),
    Input("refresh-session-view-btn", "n_clicks"),
    Input("session-selection-request", "data"),
    Input("session-delete-message", "children"),
    Input("session-progress-poll", "n_intervals"),
    State("session-select", "value"),
    State("session-select", "data"),
)
def refresh_session_options(
    _refresh_clicks: int | None,
    selection_request: dict[str, Any] | None,
    _delete_message: Any,
    _poll: int,
    selected_value: str | list[str] | None,
    current_data: list[dict[str, str]] | None,
):
    sessions = session_store.list_sessions(kind="single_backtest")
    valid_ids = {session["session_id"] for session in sessions}
    if ctx.triggered_id == "session-selection-request" and selection_request:
        requested = _selected_session_ids(selection_request.get("ids"))
    else:
        requested = _selected_session_ids(selected_value)
    selected = [session_id for session_id in requested if session_id in valid_ids]
    if not selected:
        active = next((session for session in sessions if session["state"] == "running"), None)
        if active:
            selected = [active["session_id"]]
    data = single_views.select_data(sessions)
    # The one-second ticker only publishes real state transitions. It must not
    # rebuild the selector (or downstream result tables) on every tick.
    if ctx.triggered_id == "session-progress-poll":
        if data == (current_data or []) and selected == _selected_session_ids(selected_value):
            return no_update, no_update
        return data if data != (current_data or []) else no_update, (
            selected if selected != _selected_session_ids(selected_value) else no_update
        )
    return data, selected


@callback(
    Output("session-detail", "children"),
    Input("refresh-session-view-btn", "n_clicks"),
    Input("session-select", "value"),
    Input("session-select", "data"),
)
def refresh_session_detail(
    _refresh_clicks: int | None,
    selected_value: str | list[str] | None,
    _options: list[dict[str, str]] | None,
):
    """Manual Refresh always re-reads the selected core artifact and dashboard metadata.

    A state change (draft -> running -> completed) reaches this callback through
    `session-select.data`: the option labels carry the state and the poll
    republishes them when it changes."""
    selected = _selected_sessions(selected_value)
    if len(selected) == 1:
        session = selected[0]
        execution = data_access.read_single_backtest(session["experiment"])
        events = session_store.events(session["session_id"])
        return single_views.detail(session, execution, events, FORM_OPTIONS)
    return single_views.selection_detail(selected)


@callback(
    Output("session-progress-bar", "value"),
    Output("session-progress-label", "children"),
    Output("stop-session-btn", "disabled"),
    Output("delete-selected-session-btn", "disabled"),
    Output("session-progress-poll", "disabled"),
    Output("session-active-run", "children"),
    Input("session-progress-poll", "n_intervals"),
    Input("session-select", "value"),
    Input("run-session-btn", "n_clicks"),
    Input("refresh-session-view-btn", "n_clicks"),
    Input("session-stop-message", "children"),
    Input("run-session-btn", "disabled"),
)
def refresh_selected_run_status(
    _poll: int,
    selected_value: str | list[str] | None,
    _run_clicks: int | None,
    _refresh_clicks: int | None,
    _stop_message: Any,
    run_button_disabled: bool | None,
):
    """Live status of the active run, independent of the selector's UI state.

    Never takes `session-run-message` (the background job's output) as an
    Input: Dash does not run a callback while one of its Inputs is the output
    of a callback still in flight, so the whole status panel froze for the
    entire run (see ui_common.poll_disabled)."""
    selected = _selected_sessions(selected_value)
    active = session_store.active_session(kind="single_backtest")
    runnable = bool(selected) and all(
        session["state"] in {"draft", "failed", "cancelled"} for session in selected
    ) and (len(selected) == 1 or all(session["state"] == "draft" for session in selected))
    source = active or (selected[0] if len(selected) == 1 else None)
    progress = float(source.get("progress_pct") or 0) if source else 0
    if active and active.get("cancel_requested_utc"):
        text = "Cancellation requested — waiting for the active CLI run to stop"
    elif active and len(selected) > 1 and active["session_id"] in {item["session_id"] for item in selected}:
        queue_position = next(index for index, item in enumerate(selected, 1) if item["session_id"] == active["session_id"])
        text = f"Queue {queue_position} of {len(selected)} — {active.get('progress_text') or 'Running'}"
    elif active and not any(item["session_id"] == active["session_id"] for item in selected):
        text = f"Active run: {active.get('title') or active['session_id']} — {active.get('progress_text') or 'Running'}"
    elif len(selected) > 1 and all(session["state"] in {"completed", "cached"} for session in selected):
        progress = 100
        text = f"Queue complete — {len(selected)} sessions finished"
    elif len(selected) > 1:
        text = f"{len(selected)} draft sessions selected — ready to queue"
    else:
        text = str(source.get("progress_text") or "No session selected") if source else "No session selected"
    # A background callback can be between two queued drafts with no claimed
    # session for a moment; its running button state keeps the poll alive.
    disable_polling = poll_disabled(
        bool(active) or bool(run_button_disabled)
        or (ctx.triggered_id == "run-session-btn" and runnable),
        ctx.triggered_id == "session-progress-poll",
    )
    if active and progress <= 0:
        text = f"{text} - waiting for a CLI percentage - elapsed {single_views._duration_since(active.get('started_utc'))}"
    return (
        progress,
        single_views.format_progress(progress, text),
        not bool(active) or bool(active.get("cancel_requested_utc")),
        not bool(selected) or any(session["state"] == "running" for session in selected),
        disable_polling,
        single_views.active_run_summary(active),
    )


@callback(
    Output("session-run-message", "children"),
    Input("run-session-btn", "n_clicks"),
    State("session-select", "value"),
    background=True,
    running=[(Output("run-session-btn", "disabled"), True, False)],
    prevent_initial_call=True,
)
def run_selected_session(
    _clicks: int,
    selected_value: str | list[str] | None,
):
    session_ids = _selected_session_ids(selected_value)
    if not session_ids:
        return dmc.Alert("Select one or more draft sessions before running them.", color="red")
    try:
        pending_sessions = []
        for session_id in session_ids:
            pending = session_store.get(session_id)
            if not pending:
                raise ValueError("A selected session no longer exists.")
            if pending["state"] not in {"draft", "failed", "cancelled"}:
                raise ValueError(
                    "Only draft sessions can be queued. Completed and cached sessions are for viewing; "
                    "cancelled or failed sessions can be run again one at a time."
                )
            if len(session_ids) > 1 and pending["state"] != "draft":
                raise ValueError("A multi-session queue accepts draft sessions only.")
            # Validate every queued input before the first run claims the
            # dashboard safety slot.  This does not start cTrader CLI.
            actions.validate_single_backtest_runtime(pending["plan"])
            pending_sessions.append(pending)
    except session_store.ActiveSessionError as exc:
        return dmc.Alert(str(exc), title="Dashboard safety guard", color="orange")
    except Exception as exc:
        return dmc.Alert(str(exc), title="Could not queue selected sessions", color="red")

    completed = 0
    cached = 0
    for position, pending in enumerate(pending_sessions, start=1):
        session_id = pending["session_id"]
        claimed = False
        try:
            session = session_store.claim_run(session_id)
            claimed = True

            def publish_progress(percent: float, detail: str, *, current_id: str = session_id) -> None:
                session_store.update_progress(current_id, percent, detail)

            result = actions.run_single_backtest_session(
                session["plan"],
                on_progress=publish_progress,
                should_cancel=lambda current_id=session_id: session_store.is_cancel_requested(current_id),
            )
            finished = session_store.complete(session_id, result)
            if finished["state"] == "cancelled":
                remaining = len(pending_sessions) - position
                return dmc.Alert(
                    f"Queue stopped after cancellation. {remaining} draft{'s' if remaining != 1 else ''} remain untouched.",
                    title="CANCELLED", color="orange",
                )
            if finished["state"] == "failed":
                remaining = len(pending_sessions) - position
                return dmc.Alert(
                    f"The core run was incomplete: {finished['progress_text']}. "
                    f"Run this session again to retry failed work. {remaining} queued drafts remain untouched.",
                    title="BACKTEST INCOMPLETE", color="red",
                )
            completed += 1
            cached += int(finished["state"] == "cached")
        except session_store.ActiveSessionError as exc:
            return dmc.Alert(str(exc), title="Dashboard safety guard", color="orange")
        except actions.InputFingerprintMismatch as exc:
            if claimed:
                session_store.fail(session_id, exc)
            return dmc.Alert(
                f"{exc}. Create a new session so its frozen input fingerprint matches.",
                title="INPUTS CHANGED", color="red",
            )
        except Exception as exc:
            if claimed:
                session_store.fail(session_id, exc)
            remaining = len(pending_sessions) - position
            return dmc.Alert(
                f"Queue stopped at item {position}: {exc}. "
                f"{remaining} draft{'s' if remaining != 1 else ''} remain untouched.",
                title="BACKTEST FAILED", color="red",
            )

    suffix = f" {cached} loaded from cache without calling cTrader CLI." if cached else ""
    return dmc.Alert(
        f"Completed {completed} queued draft{'s' if completed != 1 else ''}.{suffix}",
        title="QUEUE COMPLETE", color="green",
    )


@callback(
    Output("session-stop-message", "children"),
    Input("stop-session-btn", "n_clicks"),
    prevent_initial_call=True,
)
def stop_selected_session(_clicks: int):
    active = session_store.active_session(kind="single_backtest")
    if not active:
        return dmc.Alert("No Single Backtest run is currently active.", color="red")
    try:
        session_store.request_cancel(active["session_id"])
        return dmc.Alert(
            "Cancellation requested. The CLI process will stop at its next safety poll.",
            title="Stopping session", color="orange", variant="light",
        )
    except Exception as exc:
        return dmc.Alert(str(exc), title="Could not stop session", color="red")


@callback(
    Output("session-delete-message", "children"),
    Input("delete-selected-session-btn", "n_clicks"),
    State("session-select", "value"),
    prevent_initial_call=True,
)
def delete_selected_session(_clicks: int, selected_value: str | list[str] | None):
    session_ids = _selected_session_ids(selected_value)
    if not session_ids:
        return dmc.Alert("Select one or more sessions before deleting them.", color="red")
    try:
        deleted = session_store.delete_sessions(session_ids)
        return dmc.Alert(f"Deleted {deleted} dashboard session{'s' if deleted != 1 else ''}.", color="green")
    except Exception as exc:
        return dmc.Alert(str(exc), title="Could not delete session", color="red")


@callback(
    Output("session-bot-activity-table", "data"),
    Output("session-bot-activity-table", "page_count"),
    Output("session-bot-activity-table", "page_current"),
    Input("session-bot-activity-table", "page_current"),
    Input("session-bot-activity-table", "sort_by"),
    Input("session-bot-activity-table", "filter_query"),
    State("session-bot-activity-table", "page_size"),
    State("session-bot-activity-source", "data"),
    prevent_initial_call=True,
)
def page_bot_activity(
    page_current: int | None,
    sort_by: list[dict[str, str]] | None,
    filter_query: str | None,
    page_size: int | None,
    source: dict[str, Any] | None,
):
    """Server-side paging for the cBot log (Dash DataTable backend paging)."""
    events = data_access.single_bot_events(str((source or {}).get("experiment") or ""))
    rows = _sorted_rows(_filtered_rows(single_views.bot_activity_rows(events), filter_query), sort_by)
    size = max(1, int(page_size or single_views.BOT_ACTIVITY_PAGE_SIZE))
    page_count = max(1, math.ceil(len(rows) / size))
    # A new filter or sort starts from its first page.
    triggered = set(ctx.triggered_prop_ids or {})
    restart = any(key.endswith((".sort_by", ".filter_query")) for key in triggered)
    page = 0 if restart else min(max(0, int(page_current or 0)), page_count - 1)
    return rows[page * size:(page + 1) * size], page_count, page


# One clause of the DataTable filter syntax: "{column} operator value". The
# operator is matched only in that position: a value such as "line 1" must not
# be read as the "ne" operator (the substring search in the Dash docs example).
_FILTER_CLAUSE = re.compile(r"^\s*\{(?P<column>[^}]+)\}\s+(?P<operator>\S+)\s*(?P<value>.*?)\s*$")
_FILTER_ALIASES = {
    "=": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge",
}
_FILTER_TESTS = {
    "contains": lambda cell, value: value in cell,
    "datestartswith": lambda cell, value: cell.startswith(value),
    "eq": lambda cell, value: cell == value,
    "ne": lambda cell, value: cell != value,
    "lt": lambda cell, value: cell < value,
    "le": lambda cell, value: cell <= value,
    "gt": lambda cell, value: cell > value,
    "ge": lambda cell, value: cell >= value,
}


def _split_filter_part(part: str) -> tuple[str | None, str | None, str | None, bool]:
    """(column, operator, value, case_insensitive) of one filter clause."""
    match = _FILTER_CLAUSE.match(part)
    if not match:
        return None, None, None, False
    operator = _FILTER_ALIASES.get(match["operator"], match["operator"].lower())
    insensitive = False
    if operator not in _FILTER_TESTS and operator[:1] in {"i", "s"} and operator[1:] in _FILTER_TESTS:
        insensitive, operator = operator[0] == "i", operator[1:]
    if operator not in _FILTER_TESTS:
        return None, None, None, False
    value = match["value"]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"`":
        value = value[1:-1].replace("\\" + value[0], value[0])
    return match["column"], operator, value, insensitive


def _filtered_rows(rows: list[dict[str, Any]], filter_query: str | None) -> list[dict[str, Any]]:
    for part in str(filter_query or "").split(" && "):
        column, operator, value, insensitive = _split_filter_part(part)
        if column is None or operator is None or value is None:
            continue
        test = _FILTER_TESTS[operator]
        if insensitive:
            rows = [row for row in rows if test(str(row.get(column, "")).casefold(), value.casefold())]
        else:
            rows = [row for row in rows if test(str(row.get(column, "")), value)]
    return rows


def _sorted_rows(rows: list[dict[str, Any]], sort_by: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    if not sort_by:
        return rows
    column = str(sort_by[0].get("column_id") or "")
    descending = sort_by[0].get("direction") == "desc"
    # Displayed times are human-formatted; the log's own line order is the
    # chronological order.
    key = "_order" if column == "time" else column
    return sorted(rows, key=lambda row: row.get(key) if key == "_order" else str(row.get(key, "")),
                  reverse=descending)


@callback(
    Output("session-create-message", "children", allow_duplicate=True),
    Output("session-run-message", "children", allow_duplicate=True),
    Output("session-stop-message", "children", allow_duplicate=True),
    Output("session-delete-message", "children", allow_duplicate=True),
    Input("refresh-session-view-btn", "n_clicks"),
    prevent_initial_call=True,
)
def clear_old_session_messages(_clicks: int):
    """Manual Refresh presents current state without stale action banners."""
    return None, None, None, None
