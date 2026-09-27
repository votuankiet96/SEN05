"""Callback orchestration for the isolated Grid Search dashboard tab."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from dash import Input, Output, State, ctx, no_update
import dash_mantine_components as dmc

import actions
import data_access
import session_store
from . import views as grid_views


def register(app: Any, form_options: dict[str, Any]) -> None:
    """Register Grid-only callbacks on the shared Dash application."""

    def strategy_option(strategy: str) -> dict[str, Any]:
        for item in form_options["strategies"]:
            if item["value"] == strategy:
                return item
        raise ValueError(f"Strategy is not available in the Grid form: {strategy!r}")

    @app.callback(
        Output("grid-timeframe-select", "data"),
        Output("grid-timeframe-select", "value"),
        Output("grid-ksl-select", "data"),
        Output("grid-ksl-select", "value"),
        Output("grid-ksl-select", "disabled"),
        Output("grid-ktp-select", "data"),
        Output("grid-ktp-select", "value"),
        Output("grid-ktp-select", "disabled"),
        Input("grid-strategy-select", "value"),
        Input("grid-ksl-full", "checked"),
        Input("grid-ktp-full", "checked"),
        State("grid-ksl-select", "value"),
        State("grid-ktp-select", "value"),
    )
    def update_strategy(
        strategy: str, full_ksl: bool, full_ktp: bool,
        current_ksl: list[str] | None, current_ktp: list[str] | None,
    ):
        option = strategy_option(strategy)
        timeframes = option["timeframes"]
        ksl_options = option["ksl_options"]
        ktp_options = option["ktp_options"]
        valid_ksl = {str(item["value"]) for item in ksl_options}
        valid_ktp = {str(item["value"]) for item in ktp_options}
        selected_ksl = (
            [str(item["value"]) for item in ksl_options]
            if full_ksl else [str(value) for value in (current_ksl or []) if str(value) in valid_ksl]
        )
        selected_ktp = (
            [str(item["value"]) for item in ktp_options]
            if full_ktp else [str(value) for value in (current_ktp or []) if str(value) in valid_ktp]
        )
        return (
            [{"label": item.upper(), "value": item} for item in timeframes], timeframes[0],
            ksl_options, selected_ksl, bool(full_ksl),
            ktp_options, selected_ktp, bool(full_ktp),
        )

    @app.callback(
        Output("grid-start-input", "minDate"),
        Output("grid-start-input", "maxDate"),
        Output("grid-start-input", "value"),
        Output("grid-end-input", "minDate"),
        Output("grid-end-input", "maxDate"),
        Output("grid-end-input", "value"),
        Output("grid-history-status", "children"),
        Input("grid-symbol-select", "value"),
        Input("grid-timeframe-select", "value"),
        Input("grid-strategy-select", "value"),
        Input("global-signal-profile", "value"),
    )
    def update_dates(symbol: str, timeframe: str, strategy: str, redis_profile: str):
        today = date.today()
        bounds = data_access.signal_date_bounds_for_selection(
            [symbol] if symbol else [], [timeframe] if timeframe else [], strategy or "",
            redis_profile=redis_profile,
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
                f"Redis signal history: {bounds['earliest_date']} to {bounds['latest_date']}. "
                "Start date cannot be earlier than this signal-history cutoff.",
                size="sm", c="dimmed",
            ),
        )

    @app.callback(
        Output("grid-workload-summary", "children"),
        Input("grid-strategy-select", "value"),
        Input("grid-parallel-input", "value"),
        Input("grid-ksl-select", "value"),
        Input("grid-ktp-select", "value"),
    )
    def update_workload(
        strategy: str, workers: Any,
        ksl_levels: list[str] | None, ktp_levels: list[str] | None,
    ):
        return grid_views.workload_summary(
            strategy_option(strategy), workers, ksl_levels, ktp_levels,
        )

    @app.callback(
        Output("grid-selection-request", "data"),
        Output("grid-create-message", "children"),
        Input("grid-create-btn", "n_clicks"),
        State("grid-symbol-select", "value"),
        State("grid-timeframe-select", "value"),
        State("grid-strategy-select", "value"),
        State("grid-start-input", "value"),
        State("grid-end-input", "value"),
        State("grid-balance-input", "value"),
        State("grid-risk-input", "value"),
        State("grid-parallel-input", "value"),
        State("grid-ksl-select", "value"),
        State("grid-ktp-select", "value"),
        State("global-signal-profile", "value"),
        prevent_initial_call=True,
    )
    def create_grid(
        _clicks: int, symbol: str, timeframe: str, strategy: str, start: str, end: str,
        balance: float, risk: float, workers: int,
        ksl_levels: list[str] | None, ktp_levels: list[str] | None,
        redis_profile: str,
    ):
        try:
            selected_profile = session_store.confirmed_signal_profile(redis_profile)
            bounds = data_access.signal_date_bounds_for_selection(
                [symbol] if symbol else [], [timeframe] if timeframe else [], strategy or "",
                redis_profile=selected_profile,
            )
            if not bounds.get("available"):
                raise ValueError(bounds["error"])
            if not start or not end:
                raise ValueError("Select both a start date and an end date.")
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
            earliest = date.fromisoformat(bounds["earliest_date"])
            if start_date < earliest:
                raise ValueError(
                    f"Start date cannot be earlier than the Redis signal-history cutoff: {earliest.isoformat()}."
                )
            if end_date > date.today():
                raise ValueError("End date cannot be later than today.")
            plan = actions.prepare_grid_search_session(
                symbol=symbol or "", timeframe=timeframe or "", strategy=strategy or "",
                start=start, end=end, balance=float(balance), risk_percent=float(risk),
                max_parallel=workers, ksl_levels=ksl_levels or [], ktp_levels=ktp_levels or [],
                redis_profile=selected_profile,
            )
            session = session_store.create_grid_search(plan)
            return {"id": session["session_id"]}, dmc.Alert(
                f"Created a {plan['input']['pass_count']:,}-combination Grid draft. cTrader CLI has not started.",
                title="Grid inputs frozen", color="green", variant="light",
            )
        except Exception as exc:
            return no_update, dmc.Alert(str(exc), title="Could not create Grid session", color="red")

    @app.callback(
        Output("grid-session-select", "data"),
        Output("grid-session-select", "value"),
        Input("grid-refresh-btn", "n_clicks"),
        Input("grid-selection-request", "data"),
        Input("grid-delete-message", "children"),
        State("grid-session-select", "value"),
    )
    def refresh_grid_options(
        _clicks: int | None,
        selection_request: dict[str, Any] | None,
        _delete_message: Any,
        session_id: str | None,
    ):
        sessions = session_store.list_sessions(kind="grid_search")
        valid_ids = {session["session_id"] for session in sessions}
        if ctx.triggered_id == "grid-selection-request" and selection_request:
            requested = str(selection_request.get("id") or "")
        else:
            requested = str(session_id or "")
        selected = requested if requested in valid_ids else None
        return grid_views.select_data(sessions), selected

    @app.callback(
        Output("grid-session-detail", "children"),
        Input("grid-refresh-btn", "n_clicks"),
        Input("grid-session-select", "value"),
        Input("grid-progress-poll", "n_intervals"),
    )
    def refresh_grid_detail(_clicks: int | None, session_id: str | None, _poll: int):
        """Keep the running-session progress current without refreshing results every second."""
        session = session_store.get(session_id)
        if session and session.get("kind") != "grid_search":
            session = None
        # The status ticker remains available for the selected session so it
        # can recover from an old client callback response. Do not rebuild a
        # terminal session's heatmap/table on those status-only ticks.
        if session and session.get("state") != "running" and ctx.triggered_id == "grid-progress-poll":
            return no_update
        snapshot = None
        if session:
            if session.get("state") == "running":
                progress, detail = _live_grid_progress(session)
                session = {**session, "progress_pct": progress, "progress_text": detail}
                # Reading and rebuilding result tables each second is expensive and makes
                # them jump while the user is inspecting them. The Refresh button remains
                # the explicit way to load partial Grid artifacts.
                if ctx.triggered_id == "grid-refresh-btn":
                    snapshot = data_access.grid_snapshot(session["experiment"])
            else:
                snapshot = data_access.grid_snapshot(session["experiment"])
        return grid_views.detail(session, snapshot, form_options)

    @app.callback(
        Output("grid-progress-bar", "value"),
        Output("grid-progress-label", "children"),
        Output("grid-stop-btn", "disabled"),
        Output("grid-delete-btn", "disabled"),
        Output("grid-progress-poll", "disabled"),
        Input("grid-progress-poll", "n_intervals"),
        # A list refresh must also refresh the status of the selected Grid.
        # The Select value is deliberately preserved by refresh_grid_options,
        # so it does not otherwise emit a new value/change event.
        Input("grid-refresh-btn", "n_clicks"),
        Input("grid-session-select", "value"),
        Input("grid-run-btn", "n_clicks"),
        Input("grid-run-message", "children"),
        Input("grid-run-state", "data"),
        State("grid-progress-poll", "disabled"),
    )
    def refresh_grid_status(
        _poll: int,
        _refresh_clicks: int | None,
        session_id: str | None,
        _run_clicks: int | None,
        _run_message: Any,
        run_state: dict[str, Any] | None,
        polling_disabled: bool,
    ):
        session = session_store.get(session_id)
        if not session or session.get("kind") != "grid_search":
            return 0, "No Grid session selected", True, True, True
        active = session["state"] == "running"
        runnable = session["state"] in {"draft", "failed", "cancelled"}
        start_requested = bool((run_state or {}).get("active"))
        if active:
            progress, text = _live_grid_progress(session)
        elif start_requested and runnable:
            progress = 0.0
            text = "Starting Grid: checking the frozen configuration and cTrader CLI"
        else:
            progress = float(session.get("progress_pct") or 0)
            text = str(session.get("progress_text") or session["state"].title())
        # This is deliberately a lightweight status ticker only: it reads one
        # dashboard row and aggregated ExperimentStore counts. Keeping it on
        # while a session is selected prevents an old browser callback response
        # (for example, a preceding cancellation) from freezing Stop/progress
        # controls after a subsequent run is claimed. Result tables are not
        # refreshed by this ticker.
        return progress, f"{_truncate(progress)}% - {text}", not active, active, False

    @app.callback(
        Output("grid-run-message", "children"),
        Input("grid-run-btn", "n_clicks"),
        State("grid-session-select", "value"),
        background=True,
        running=[
            (Output("grid-run-btn", "disabled"), True, False),
            (Output("grid-run-state", "data"), {"active": True}, {"active": False}),
        ],
        prevent_initial_call=True,
    )
    def run_grid(_clicks: int, session_id: str | None):
        if not session_id:
            return dmc.Alert("Select a Grid draft before running it.", color="red")
        claimed = False
        try:
            pending = session_store.get(session_id)
            if not pending or pending.get("kind") != "grid_search":
                raise ValueError("The selected Grid session no longer exists.")
            if pending["state"] not in {"draft", "failed", "cancelled"}:
                raise ValueError(
                    "Only a draft, failed or cancelled Grid can run. Completed and cached Grids are for viewing."
                )
            actions.validate_grid_search_runtime(pending["plan"])
            session = session_store.claim_run(session_id)
            claimed = True

            result = actions.run_grid_search_session(
                session["plan"],
                # Core reports progress for one CLI worker only. Persisting it
                # as session progress made the detail view misleading; the
                # status callback derives the aggregate from completed runs.
                should_cancel=lambda: session_store.is_cancel_requested(session_id),
            )
            finished = session_store.complete(session_id, result)
            if finished["state"] == "cancelled":
                return dmc.Alert(
                    "The Grid stopped at the next safe CLI poll. Completed combination artifacts were retained.",
                    title="GRID CANCELLED", color="orange",
                )
            if finished["state"] == "failed":
                return dmc.Alert(
                    f"{finished['progress_text']}. Run this Grid session again to retry only missing or failed combinations.",
                    title="GRID INCOMPLETE", color="red",
                )
            cached = finished["state"] == "cached"
            return dmc.Alert(
                "Loaded the existing pipeline result without calling cTrader CLI."
                if cached else f"Completed {int(result.get('total') or 0):,} Grid combinations.",
                title="GRID CACHED" if cached else "GRID COMPLETE",
                color="teal" if cached else "green",
            )
        except session_store.ActiveSessionError as exc:
            return dmc.Alert(str(exc), title="Dashboard safety guard", color="orange")
        except actions.InputFingerprintMismatch as exc:
            if claimed:
                session_store.fail(session_id, exc)
            return dmc.Alert(
                f"{exc}. Create a new Grid session so its frozen input fingerprint matches.",
                title="INPUTS CHANGED", color="red",
            )
        except Exception as exc:
            # Input/preflight errors must not rewrite an existing completed or
            # cached session as failed. Only a session already claimed by this
            # callback owns an execution failure transition.
            if claimed:
                session_store.fail(session_id, exc)
            return dmc.Alert(str(exc), title="GRID FAILED", color="red")

    @app.callback(
        Output("grid-stop-message", "children"),
        Input("grid-stop-btn", "n_clicks"),
        State("grid-session-select", "value"),
        prevent_initial_call=True,
    )
    def stop_grid(_clicks: int, session_id: str | None):
        session = session_store.get(session_id)
        if not session or session.get("kind") != "grid_search" or session.get("state") != "running":
            return dmc.Alert("Select the active Grid before requesting a stop.", color="red")
        try:
            session_store.request_cancel(session_id)
            return dmc.Alert(
                "Cancellation requested. Active CLI workers will stop at their next safety poll.",
                title="Stopping Grid", color="orange", variant="light",
            )
        except Exception as exc:
            return dmc.Alert(str(exc), title="Could not stop Grid", color="red")

    @app.callback(
        Output("grid-delete-message", "children"),
        Input("grid-delete-btn", "n_clicks"),
        State("grid-session-select", "value"),
        prevent_initial_call=True,
    )
    def delete_grid(_clicks: int, session_id: str | None):
        if not session_id:
            return dmc.Alert("Select a Grid session before deleting it.", color="red")
        session = session_store.get(session_id)
        if not session or session.get("kind") != "grid_search":
            return dmc.Alert("The selected Grid session no longer exists.", color="red")
        try:
            session_store.delete_sessions([session_id])
            return dmc.Alert("Deleted 1 dashboard Grid session.", color="green")
        except Exception as exc:
            return dmc.Alert(str(exc), title="Could not delete Grid session", color="red")


def _truncate(value: float) -> str:
    return f"{int(value * 100) / 100:,.2f}"


def _live_grid_progress(session: dict[str, Any]) -> tuple[float, str]:
    """Return aggregate Grid progress from persisted per-combination run states.

    The core progress hook belongs to one worker and is therefore not a valid Grid
    percentage. The ExperimentStore rows are the authoritative aggregate source.
    Elapsed time and ETA are presentation-only estimates; they never drive progress.
    """
    total = int((session.get("plan", {}).get("input") or {}).get("pass_count") or 0)
    if total <= 0:
        return 0.0, "Preparing Grid combinations"

    execution = data_access.grid_execution_status(str(session["experiment"]))
    finished = min(total, int(execution.get("finished") or 0))
    active = int(execution.get("running") or 0)
    progress = finished / total * 100
    text = f"{finished:,} / {total:,} combinations finished"
    if active:
        text += f" - {active:,} active"

    elapsed_seconds = _elapsed_seconds(session.get("started_utc"))
    if elapsed_seconds is not None:
        text += f" - elapsed {_duration(elapsed_seconds)}"
        # Completion rate already includes the configured worker parallelism.
        # Wait for a few completed combinations before presenting an ETA so the
        # first unusually short or long run does not mislead the user.
        if finished >= 3 and finished < total:
            eta_seconds = elapsed_seconds * (total - finished) / finished
            text += f" - estimated remaining {_duration(eta_seconds)}"
    return progress, text


def _elapsed_seconds(started_utc: Any) -> float | None:
    if not started_utc:
        return None
    try:
        started = datetime.fromisoformat(str(started_utc))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:d}m {seconds:02d}s"
    return f"{seconds:d}s"
