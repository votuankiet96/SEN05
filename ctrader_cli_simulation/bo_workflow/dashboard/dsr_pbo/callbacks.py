"""Callback orchestration for the DSR + PBO dashboard tab."""
from __future__ import annotations

from typing import Any

from dash import Input, Output, State, ctx, no_update
import dash_mantine_components as dmc

import actions
import data_access
import session_store
from ui_common import poll_disabled, truncated
from . import views


def register(app: Any) -> None:
    @app.callback(
        Output("diagnostics-source-select", "data"),
        Output("diagnostics-source-select", "value"),
        Input("diagnostics-source-refresh-btn", "n_clicks"),
        State("diagnostics-source-select", "value"),
    )
    def refresh_sources(_clicks: int | None, selected: str | None):
        sessions = session_store.list_sessions(kind="grid_search")
        options = views.source_options(sessions)
        valid = {item["value"] for item in options}
        return options, selected if selected in valid else None

    @app.callback(
        Output("diagnostics-source-status", "children"),
        Input("diagnostics-source-select", "value"),
        Input("diagnostics-min-trades-input", "value"),
    )
    def inspect_source(source_id: str | None, min_trades: Any):
        session = session_store.get(source_id)
        minimum = _nonnegative_int(min_trades)
        snapshot = (
            data_access.grid_diagnostics_source_snapshot(
                session["experiment"], min_trades=minimum,
            )
            if session and session.get("kind") == "grid_search"
            else None
        )
        return views.source_status(
            session,
            snapshot,
            minimum,
            actions.completed_execution_policy_available(),
        )

    @app.callback(
        Output("diagnostics-selection-request", "data"),
        Output("diagnostics-create-message", "children"),
        Input("diagnostics-create-btn", "n_clicks"),
        State("diagnostics-source-select", "value"),
        State("diagnostics-min-trades-input", "value"),
        State("diagnostics-blocks-input", "value"),
        State("diagnostics-objective-select", "value"),
        prevent_initial_call=True,
    )
    def create_diagnostics(
        _clicks: int, source_id: str | None, min_trades: Any, blocks: Any, objective: str | None,
    ):
        try:
            if not actions.completed_execution_policy_available():
                raise RuntimeError(
                    "The installed core has not yet handed off the completed-execution "
                    "DSR/PBO policy. No diagnostics draft was created."
                )
            source = session_store.get(source_id)
            if not source or source.get("kind") != "grid_search":
                raise ValueError("Select a saved Grid source session")
            minimum = _nonnegative_int(min_trades)
            snapshot = data_access.grid_diagnostics_source_snapshot(
                source["experiment"], min_trades=minimum,
            )
            plan = actions.prepare_grid_diagnostics_session(
                source_session=source,
                source_snapshot=snapshot,
                min_trades=minimum,
                blocks=int(blocks),
                objective=str(objective or "net_profit"),
            )
            session = session_store.create_grid_diagnostics(plan)
            return {"id": session["session_id"]}, dmc.Alert(
                "Created a DSR/PBO draft. No analysis or cTrader process has started.",
                title="Diagnostics inputs frozen", color="green", variant="light",
            )
        except Exception as exc:
            return no_update, dmc.Alert(
                str(exc), title="Could not create diagnostics session", color="red",
            )

    @app.callback(
        Output("diagnostics-session-select", "data"),
        Output("diagnostics-session-select", "value"),
        Input("diagnostics-refresh-btn", "n_clicks"),
        Input("diagnostics-selection-request", "data"),
        Input("diagnostics-delete-message", "children"),
        State("diagnostics-session-select", "value"),
    )
    def refresh_sessions(
        _clicks: int | None,
        selection_request: dict[str, Any] | None,
        _delete_message: Any,
        selected: str | None,
    ):
        session_store.reconcile_abandoned_sessions()
        sessions = session_store.list_sessions(kind="dsr_pbo")
        valid = {session["session_id"] for session in sessions}
        if ctx.triggered_id == "diagnostics-selection-request" and selection_request:
            requested = str(selection_request.get("id") or "")
        else:
            requested = str(selected or "")
        target = requested if requested in valid else None
        if target is None:
            active = next((item for item in sessions if item["state"] == "running"), None)
            target = active["session_id"] if active else None
        return views.session_options(sessions), target

    @app.callback(
        Output("diagnostics-session-detail", "children"),
        Input("diagnostics-refresh-btn", "n_clicks"),
        Input("diagnostics-session-select", "value"),
        Input("diagnostics-progress-poll", "n_intervals"),
    )
    def refresh_detail(
        _clicks: int | None, session_id: str | None, _poll: int,
    ):
        session = session_store.get(session_id)
        # Live progress is shown by refresh_status; while the analysis runs a
        # tick must not rebuild this panel. A tick after it ends renders the
        # results once, then refresh_status stops the ticker. The background
        # job's `diagnostics-run-message` is deliberately not an Input (see
        # ui_common.poll_disabled).
        if ctx.triggered_id == "diagnostics-progress-poll" and (not session or session.get("state") == "running"):
            return no_update
        return views.detail(session)

    @app.callback(
        Output("diagnostics-progress-bar", "value"),
        Output("diagnostics-progress-label", "children"),
        Output("diagnostics-stop-btn", "disabled"),
        Output("diagnostics-delete-btn", "disabled"),
        Output("diagnostics-progress-poll", "disabled"),
        Input("diagnostics-progress-poll", "n_intervals"),
        Input("diagnostics-session-select", "value"),
        Input("diagnostics-run-btn", "n_clicks"),
        Input("diagnostics-refresh-btn", "n_clicks"),
        # True while the background analysis runs (its `running` flag): keeps
        # the ticker alive in the seconds before the worker claims the session,
        # when a failed/cancelled session being re-run still reads as terminal.
        Input("diagnostics-run-btn", "disabled"),
    )
    def refresh_status(
        _poll: int,
        session_id: str | None,
        _run_clicks: int | None,
        _refresh_clicks: int | None,
        run_button_disabled: bool | None,
    ):
        is_tick = ctx.triggered_id == "diagnostics-progress-poll"
        # Reconciling takes a write lock; do it on user actions, not every tick
        # (it also runs at app start, on refresh and before every claim).
        if not is_tick:
            session_store.reconcile_abandoned_sessions()
        session = session_store.get(session_id)
        if not session or session.get("kind") != "dsr_pbo":
            return 0, "No diagnostics session selected", True, True, True
        active = session["state"] == "running"
        progress = float(session.get("progress_pct") or 0)
        text = str(session.get("progress_text") or session["state"].title())
        runnable = session["state"] in {"draft", "failed", "cancelled"}
        keep_polling = (
            active or bool(run_button_disabled)
            or (ctx.triggered_id == "diagnostics-run-btn" and runnable)
        )
        return (
            progress, f"{truncated(progress):.2f}% - {text}", not active, active,
            poll_disabled(keep_polling, is_tick),
        )

    @app.callback(
        Output("diagnostics-run-message", "children"),
        Input("diagnostics-run-btn", "n_clicks"),
        State("diagnostics-session-select", "value"),
        background=True,
        cancel=[Input("diagnostics-stop-btn", "n_clicks")],
        running=[(Output("diagnostics-run-btn", "disabled"), True, False)],
        prevent_initial_call=True,
    )
    def run_diagnostics(_clicks: int, session_id: str | None):
        if not session_id:
            return dmc.Alert("Select a diagnostics draft before running it.", color="red")
        claimed = False
        try:
            pending = session_store.get(session_id)
            if not pending or pending.get("kind") != "dsr_pbo":
                raise ValueError("The selected diagnostics session no longer exists")
            if pending["state"] not in {"draft", "failed", "cancelled"}:
                raise ValueError("Only a draft, failed or cancelled diagnostics session can run")
            source_id = str((pending.get("plan", {}).get("input") or {}).get("source_session_id") or "")
            source = session_store.get(source_id)
            if not source:
                raise ValueError("The source Grid session no longer exists")
            minimum = int((pending["plan"].get("input") or {}).get("min_trades") or 0)
            snapshot = data_access.grid_diagnostics_source_snapshot(
                source["experiment"], min_trades=minimum,
            )
            actions.validate_grid_diagnostics_source(pending["plan"], source, snapshot)
            session = session_store.claim_run(session_id)
            claimed = True

            def publish_progress(percent: float, detail: str) -> None:
                session_store.update_progress(session_id, percent, detail)

            result = actions.run_grid_diagnostics_session(
                session["plan"], on_progress=publish_progress,
            )
            finished = session_store.complete(session_id, result)
            if finished["state"] == "cancelled":
                return dmc.Alert("Analysis was cancelled.", title="CANCELLED", color="orange")
            return dmc.Alert(
                "Loaded both diagnostics from cache." if finished["state"] == "cached"
                else "DSR and PBO completed from the saved Grid reports.",
                title="DIAGNOSTICS COMPLETE", color="green",
            )
        except session_store.ActiveSessionError as exc:
            return dmc.Alert(str(exc), title="Analysis safety guard", color="orange")
        except Exception as exc:
            if claimed:
                session_store.fail(session_id, exc)
            return dmc.Alert(str(exc), title="DIAGNOSTICS FAILED", color="red")

    @app.callback(
        Output("diagnostics-stop-message", "children"),
        Input("diagnostics-stop-btn", "n_clicks"),
        State("diagnostics-session-select", "value"),
        prevent_initial_call=True,
    )
    def stop_diagnostics(_clicks: int, session_id: str | None):
        try:
            session_store.cancel_analysis(str(session_id or ""))
            return dmc.Alert(
                "The analysis worker was cancelled. No cTrader process was involved.",
                title="Analysis cancelled", color="orange", variant="light",
            )
        except Exception as exc:
            return dmc.Alert(str(exc), title="Could not cancel analysis", color="red")

    @app.callback(
        Output("diagnostics-delete-message", "children"),
        Input("diagnostics-delete-btn", "n_clicks"),
        State("diagnostics-session-select", "value"),
        prevent_initial_call=True,
    )
    def delete_diagnostics(_clicks: int, session_id: str | None):
        if not session_id:
            return dmc.Alert("Select a diagnostics session before deleting it.", color="red")
        try:
            session_store.delete_sessions([session_id])
            return dmc.Alert("Deleted 1 dashboard diagnostics session.", color="green")
        except Exception as exc:
            return dmc.Alert(str(exc), title="Could not delete diagnostics session", color="red")

    @app.callback(
        Output("diagnostics-create-message", "children", allow_duplicate=True),
        Output("diagnostics-run-message", "children", allow_duplicate=True),
        Output("diagnostics-stop-message", "children", allow_duplicate=True),
        Output("diagnostics-delete-message", "children", allow_duplicate=True),
        Input("diagnostics-refresh-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_old_diagnostics_messages(_clicks: int):
        return None, None, None, None


def _nonnegative_int(value: Any) -> int:
    number = int(value)
    if number < 0 or number != float(value):
        raise ValueError("Minimum trades must be a non-negative integer")
    return number
