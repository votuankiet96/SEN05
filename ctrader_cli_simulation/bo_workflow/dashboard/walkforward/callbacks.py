"""Callback orchestration for the independent Walk-forward dashboard tab."""
from __future__ import annotations

from datetime import date
from typing import Any

from dash import Input, Output, State, ctx, no_update
import dash_mantine_components as dmc

import actions
import data_access
import session_store
from . import views


def register(app: Any, form_options: dict[str, Any]) -> None:
    def strategy_option(value: str) -> dict[str, Any]:
        for item in form_options["strategies"]:
            if item["value"] == value:
                return item
        raise ValueError(f"Strategy is not available in the Walk-forward form: {value!r}")

    @app.callback(
        Output("wf-timeframe-select", "data"), Output("wf-timeframe-select", "value"),
        Input("wf-strategy-select", "value"),
    )
    def update_strategy(strategy: str):
        timeframes = strategy_option(strategy)["timeframes"]
        return [{"label": item.upper(), "value": item} for item in timeframes], timeframes[0]

    @app.callback(
        Output("wf-is-months-input", "value"), Output("wf-oos-months-input", "value"),
        Input("wf-window-preset", "value"),
    )
    def apply_window_preset(preset: str):
        try:
            is_months, oos_months = (int(value) for value in str(preset).split(":", 1))
            return is_months, oos_months
        except (TypeError, ValueError):
            return 12, 4

    @app.callback(
        Output("wf-step-months-input", "value"),
        Input("wf-window-preset", "value"), Input("wf-oos-months-input", "value"),
    )
    def default_step_to_oos(preset: str, oos_months: Any):
        if ctx.triggered_id == "wf-window-preset":
            try:
                return int(str(preset).split(":", 1)[1])
            except (TypeError, ValueError):
                return 4
        return oos_months

    @app.callback(
        Output("wf-start-input", "minDate"), Output("wf-start-input", "maxDate"), Output("wf-start-input", "value"),
        Output("wf-end-input", "minDate"), Output("wf-end-input", "maxDate"), Output("wf-end-input", "value"),
        Output("wf-history-status", "children"),
        Input("wf-symbol-select", "value"), Input("wf-timeframe-select", "value"), Input("wf-strategy-select", "value"),
        Input("global-signal-profile", "value"),
    )
    def update_dates(symbol: str, timeframe: str, strategy: str, redis_profile: str):
        today = date.today()
        bounds = data_access.signal_date_bounds_for_selection(
            [symbol] if symbol else [], [timeframe] if timeframe else [], strategy or "",
            redis_profile=redis_profile,
        )
        if not bounds.get("available"):
            return "2000-01-01", today.isoformat(), None, "2000-01-01", today.isoformat(), None, dmc.Alert(bounds["error"], title="Signal history unavailable", color="red", variant="light")
        earliest = date.fromisoformat(bounds["earliest_date"])
        default_start = max(earliest, date(2025, 1, 1))
        if default_start >= today:
            return earliest.isoformat(), today.isoformat(), None, earliest.isoformat(), today.isoformat(), None, dmc.Alert("Signal history does not contain a complete selectable date range.", title="Signal history unavailable", color="red", variant="light")
        return (earliest.isoformat(), today.isoformat(), default_start.isoformat(), earliest.isoformat(), today.isoformat(), today.isoformat(),
                dmc.Text(f"Redis signal history: {bounds['earliest_date']} to {bounds['latest_date']}. Start date cannot precede this cutoff.", size="sm", c="dimmed"))

    @app.callback(Output("wf-commission-warning", "children"), Input("wf-symbol-select", "value"))
    def show_commission_warning(symbol: str):
        message = data_access.commission_warning(symbol or "")
        return dmc.Alert(message, title="Commission warning", color="orange", variant="light") if message else html_empty()

    @app.callback(
        Output("wf-workload-summary", "children"),
        Input("wf-strategy-select", "value"), Input("wf-symbol-select", "value"), Input("wf-timeframe-select", "value"),
        Input("wf-start-input", "value"), Input("wf-end-input", "value"),
        Input("wf-is-months-input", "value"), Input("wf-oos-months-input", "value"), Input("wf-step-months-input", "value"),
        Input("wf-parallel-input", "value"), Input("wf-objectives-select", "value"),
        Input("global-signal-profile", "value"),
    )
    def update_workload(strategy: str, symbol: str, timeframe: str, start: str, end: str, is_months: Any, oos_months: Any, step_months: Any, workers: Any, objectives: list[str] | None, redis_profile: str):
        try:
            if not start or not end:
                raise ValueError("Select both dates to preview the workload.")
            if int(step_months) != int(oos_months):
                raise ValueError("Step months must equal Out-of-sample months in protocol v1.")
            if date.fromisoformat(start).day > 28:
                raise ValueError("Start date must be on day 1-28 so OOS windows do not overlap or leave gaps.")
            windows = actions.preview_walkforward_windows(start=start, end=end, is_months=int(is_months), oos_months=int(oos_months), step_months=int(step_months))
            option = strategy_option(strategy)
            full_grid = len(option["ksl_options"]) * len(option["ktp_options"])
            budget = data_access.cli_launch_budget()
            objective_count = len(objectives or [])
            if not 1 <= objective_count <= 3:
                raise ValueError("Select between one and three objectives.")
            try:
                signal_counts = data_access.walkforward_train_signal_counts(
                    symbol, timeframe, strategy, windows, redis_profile=redis_profile,
                )
                low_signal_windows = sum(count < 30 for count in signal_counts)
            except Exception:
                low_signal_windows = 0
                signal_preflight_unavailable = True
            else:
                signal_preflight_unavailable = False
            return views.workload_summary(windows=len(windows), full_grid=full_grid, objectives=objective_count,
                                          workers=max(1, int(workers)), budget=budget, low_signal_windows=low_signal_windows,
                                          signal_preflight_unavailable=signal_preflight_unavailable)
        except Exception as exc:
            return dmc.Alert(str(exc), title="Walk-forward preview unavailable", color="red", variant="light")

    @app.callback(
        Output("wf-selection-request", "data"), Output("wf-create-message", "children"),
        Input("wf-create-btn", "n_clicks"),
        State("wf-symbol-select", "value"), State("wf-timeframe-select", "value"), State("wf-strategy-select", "value"),
        State("wf-start-input", "value"), State("wf-end-input", "value"), State("wf-balance-input", "value"),
        State("wf-risk-input", "value"), State("wf-parallel-input", "value"), State("wf-is-months-input", "value"),
        State("wf-oos-months-input", "value"), State("wf-step-months-input", "value"), State("wf-objectives-select", "value"),
        State("wf-primary-objective-select", "value"), State("wf-primary-rule-select", "value"),
        State("global-signal-profile", "value"),
        prevent_initial_call=True,
    )
    def create_session(_clicks: int, symbol: str, timeframe: str, strategy: str, start: str, end: str, balance: Any, risk: Any, workers: Any, is_months: Any, oos_months: Any, step_months: Any, objectives: list[str] | None, primary_objective: str | None, primary_rule: str | None, redis_profile: str):
        try:
            selected_profile = session_store.confirmed_signal_profile(redis_profile)
            _validate_dates(symbol, timeframe, strategy, start, end, selected_profile)
            selected = [str(value) for value in (objectives or [])]
            if not 1 <= len(selected) <= 3 or any(value not in actions.WALKFORWARD_OBJECTIVES for value in selected):
                raise ValueError("Select between one and three supported objectives.")
            if primary_objective not in selected or primary_rule not in {"plateau", "best"}:
                raise ValueError("Choose a primary objective from the selected objectives and a primary selection rule before creating drafts.")
            plans = []
            fingerprint = None
            prior_oos_exists = False
            for objective in selected:
                plan = actions.prepare_walkforward_session(symbol=symbol or "", timeframe=timeframe or "", strategy=strategy or "", start=start, end=end, balance=float(balance), risk_percent=float(risk), max_parallel=int(workers), is_months=int(is_months), oos_months=int(oos_months), step_months=int(step_months), objective=objective, frozen_input_fingerprint=fingerprint, redis_profile=selected_profile)
                plan["interpretation"] = {"primary_objective": primary_objective, "primary_rule": primary_rule,
                                          "role": "primary" if objective == primary_objective else "exploratory"}
                prior_oos_exists = prior_oos_exists or bool((data_access.walkforward_execution_status(plan["experiment"]).get("oos") or {}).get("rows"))
                fingerprint = plan["preflight"]["input_fingerprint"]
                plans.append(plan)
            if prior_oos_exists:
                for plan in plans:
                    plan["interpretation"]["role"] = "retrospective"
            sessions = session_store.create_walkforward_group(plans)
            first = sessions[0]
            labels = ", ".join(value.replace("_", " ").title() for value in selected)
            review_note = " Existing OOS data was found, so these choices are marked retrospective, not pre-registered." if prior_oos_exists else ""
            return {"id": first["session_id"]}, dmc.Alert(f"Created {len(sessions):,} independent Walk-forward draft(s) for: {labels}. No new backtest has started; input fingerprinting may have checked the CLI version.{review_note}", title="Walk-forward inputs frozen", color="orange" if prior_oos_exists else "green", variant="light")
        except Exception as exc:
            return no_update, dmc.Alert(str(exc), title="Could not create Walk-forward session", color="red")

    @app.callback(
        Output("wf-session-select", "data"), Output("wf-session-select", "value"),
        Input("wf-refresh-btn", "n_clicks"), Input("wf-selection-request", "data"), Input("wf-delete-message", "children"),
        State("wf-session-select", "value"),
    )
    def refresh_options(_clicks: int | None, requested: dict[str, Any] | None, _delete: Any, selected: str | None):
        sessions = session_store.list_sessions(kind="walkforward")
        ids = {item["session_id"] for item in sessions}
        target = str(requested.get("id") or "") if ctx.triggered_id == "wf-selection-request" and requested else str(selected or "")
        if target not in ids:
            running = next((item for item in sessions if item.get("state") == "running"), None)
            target = str(running["session_id"]) if running else ""
        return views.select_data(sessions), target if target in ids else None

    @app.callback(
        Output("wf-session-detail", "children"),
        Input("wf-refresh-btn", "n_clicks"), Input("wf-session-select", "value"), Input("wf-progress-poll", "n_intervals"), Input("wf-run-message", "children"),
    )
    def refresh_detail(_clicks: int | None, session_id: str | None, _poll: int, _run_message: Any):
        if ctx.triggered_id == "wf-progress-poll":
            return no_update
        session = session_store.get(session_id)
        if session and session.get("kind") != "walkforward": session = None
        progress = data_access.walkforward_execution_status(session["experiment"]) if session else None
        rows = data_access.walkforward_results(session["experiment"]) if session and session.get("state") != "draft" else []
        return views.detail(session, progress, rows, form_options)

    @app.callback(
        Output("wf-live-progress", "children"), Output("wf-run-btn", "disabled"), Output("wf-stop-btn", "disabled"), Output("wf-delete-btn", "disabled"), Output("wf-progress-poll", "disabled"),
        Output("wf-budget-confirmation", "style"), Output("wf-budget-confirm", "label"), Output("wf-budget-confirm", "checked"),
        Input("wf-progress-poll", "n_intervals"), Input("wf-refresh-btn", "n_clicks"), Input("wf-session-select", "value"), Input("wf-run-message", "children"), Input("wf-run-state", "data"),
    )
    def refresh_status(_poll: int, _refresh: int | None, session_id: str | None, _message: Any, run_state: dict[str, Any] | None):
        session = session_store.get(session_id)
        if session and session.get("kind") != "walkforward":
            session = None
        active_session = session_store.active_session(kind="walkforward")
        starting = bool((run_state or {}).get("active"))
        progress = data_access.walkforward_execution_status(active_session["experiment"]) if active_session else None
        live_card = views.live_progress(active_session, progress, starting=starting and not active_session,
                                        selected_session_id=session_id)
        can_run = bool(session and session.get("state") in {"draft", "failed", "cancelled"})
        selected_running = bool(session and session.get("state") == "running")
        confirmation_style, confirmation_label = {"display": "none"}, ""
        if can_run and not active_session:
            input_doc = (session.get("plan") or {}).get("input") or {}
            required = int(input_doc.get("train_backtests") or 0) + int(input_doc.get("max_oos_backtests") or 0)
            remaining = data_access.cli_launch_budget()["remaining"]
            if required > remaining:
                confirmation_style = {"display": "block"}
                confirmation_label = (f"This selected session plans up to {required:,} backtests; only {remaining:,} CLI launches remain today. "
                                      "I understand it may stop and need a retry after 00:00 Europe/Prague.")
        return (live_card, bool(active_session) or starting or not can_run,
                not selected_running, selected_running, not (active_session or starting),
                confirmation_style, confirmation_label, False)

    @app.callback(
        Output("wf-run-message", "children"), Input("wf-run-btn", "n_clicks"), State("wf-session-select", "value"), State("wf-budget-confirm", "checked"),
        background=True, running=[(Output("wf-run-state", "data"), {"active": True}, {"active": False})], prevent_initial_call=True,
    )
    def run_session(_clicks: int, session_id: str | None, budget_confirmed: bool):
        if not session_id: return dmc.Alert("Select a Walk-forward draft before running it.", color="red")
        claimed = False
        try:
            pending = session_store.get(session_id)
            if not pending or pending.get("kind") != "walkforward": raise ValueError("The selected Walk-forward session no longer exists.")
            if pending.get("state") not in {"draft", "failed", "cancelled"}: raise ValueError("Only a draft, failed or cancelled Walk-forward session can run. Completed and cached sessions are for viewing; changed inputs require a new draft.")
            input_doc = pending.get("plan", {}).get("input", {})
            budget = data_access.cli_launch_budget()
            required = int(input_doc.get("train_backtests") or 0) + int(input_doc.get("max_oos_backtests") or 0)
            if required > budget["remaining"] and not budget_confirmed:
                raise ValueError("This session exceeds today's remaining CLI allowance. Confirm that it may resume after 00:00 Europe/Prague.")
            actions.validate_walkforward_runtime(pending["plan"])
            session = session_store.claim_run(session_id); claimed = True
            result = actions.run_walkforward_session(session["plan"], should_cancel=lambda: session_store.is_cancel_requested(session_id))
            finished = session_store.complete(session_id, result)
            if finished["state"] == "cancelled": return dmc.Alert("The Walk-forward session stopped at its next safety poll. Completed artifacts were retained.", title="WALK-FORWARD CANCELLED", color="orange")
            if finished["state"] == "failed":
                title = "DAILY CLI BUDGET REACHED" if result.get("daily_cli_budget_exhausted") else "WALK-FORWARD INCOMPLETE"
                message = "Daily CLI budget reached. Retry after 00:00 Europe/Prague; only missing work will be attempted." if result.get("daily_cli_budget_exhausted") else "The session is incomplete. Run it again to retry only missing or failed work."
                return dmc.Alert(message, title=title, color="red")
            without_pick = int(result.get("windows_without_a_pick") or 0)
            if without_pick:
                return dmc.Alert(f"Walk-forward execution completed, but {without_pick:,} window-rule decision(s) had no eligible parameter pick. Inspect the OOS table before interpreting the result.", title="COMPLETED WITH MISSING PICKS", color="orange")
            return dmc.Alert("Loaded the existing pipeline result without calling cTrader CLI." if finished["state"] == "cached" else "Walk-forward completed.", title="WALK-FORWARD CACHED" if finished["state"] == "cached" else "WALK-FORWARD COMPLETE", color="teal" if finished["state"] == "cached" else "green")
        except session_store.ActiveSessionError as exc:
            return dmc.Alert(str(exc), title="Dashboard safety guard", color="orange")
        except actions.InputFingerprintMismatch as exc:
            if claimed: session_store.mark_inputs_changed(session_id, exc)
            return dmc.Alert(f"{exc}. Create a new Walk-forward session so its frozen inputs match.", title="INPUTS CHANGED", color="red")
        except Exception as exc:
            if claimed: session_store.fail(session_id, exc)
            return dmc.Alert(str(exc), title="WALK-FORWARD FAILED", color="red")

    @app.callback(Output("wf-stop-message", "children"), Input("wf-stop-btn", "n_clicks"), State("wf-session-select", "value"), prevent_initial_call=True)
    def stop_session(_clicks: int, session_id: str | None):
        try:
            session = session_store.get(session_id)
            if not session or session.get("kind") != "walkforward" or session.get("state") != "running": raise ValueError("Select the active Walk-forward session before requesting a stop.")
            session_store.request_cancel(session_id)
            return dmc.Alert("Cancellation requested. Active CLI workers will stop at their next safety poll.", title="Stopping Walk-forward", color="orange", variant="light")
        except Exception as exc: return dmc.Alert(str(exc), title="Could not stop Walk-forward", color="red")

    @app.callback(Output("wf-delete-message", "children"), Input("wf-delete-btn", "n_clicks"), State("wf-session-select", "value"), prevent_initial_call=True)
    def delete_session(_clicks: int, session_id: str | None):
        try:
            session = session_store.get(session_id)
            if not session or session.get("kind") != "walkforward": raise ValueError("The selected Walk-forward session no longer exists.")
            session_store.delete_sessions([session_id])
            return dmc.Alert("Deleted 1 dashboard Walk-forward session.", color="green")
        except Exception as exc: return dmc.Alert(str(exc), title="Could not delete Walk-forward session", color="red")


def _validate_dates(symbol: str, timeframe: str, strategy: str, start: str, end: str, redis_profile: str) -> None:
    if not start or not end: raise ValueError("Select both a start date and an end date.")
    if date.fromisoformat(start).day > 28: raise ValueError("Start date must be on day 1-28 so OOS windows do not overlap or leave gaps.")
    bounds = data_access.signal_date_bounds_for_selection(
        [symbol] if symbol else [], [timeframe] if timeframe else [], strategy or "",
        redis_profile=redis_profile,
    )
    if not bounds.get("available"): raise ValueError(bounds["error"])
    if date.fromisoformat(start) < date.fromisoformat(bounds["earliest_date"]): raise ValueError(f"Start date cannot be earlier than the Redis signal-history cutoff: {bounds['earliest_date']}.")
    if date.fromisoformat(end) > date.today(): raise ValueError("End date cannot be later than today.")


def html_empty() -> Any:
    from dash import html
    return html.Div()
