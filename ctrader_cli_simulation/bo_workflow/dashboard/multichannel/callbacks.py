"""Callbacks for explicit-row shared-account Multi-channel Backtests."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from dash import ALL, MATCH, Input, Output, State, ctx, no_update
import dash_mantine_components as dmc

import actions
import data_access
import session_store
from ui_common import poll_disabled
from single import callbacks as single_callbacks
from single import views as single_views
from . import views as multi_views


def _form_rows(
    stored: list[dict[str, Any]] | None,
    strategies: list[Any], symbols: list[Any], timeframes: list[Any], risks: list[Any],
    sources: list[Any] | None = None,
) -> list[dict[str, Any]]:
    rows = list(stored or [])
    picked = list(sources or [])
    result = []
    for index, row in enumerate(rows):
        if index >= min(len(strategies), len(symbols), len(timeframes), len(risks)):
            continue
        item = {
            "id": int(row["id"]),
            "strategy": strategies[index], "symbol": symbols[index],
            "timeframe": timeframes[index], "risk_percent": risks[index],
        }
        # A row's own Signal source overrides the page source; "default" means "follow the page".
        source = picked[index] if index < len(picked) else None
        if source and source != multi_views.SOURCE_PAGE_DEFAULT:
            item["source"] = source
        result.append(item)
    return result


def register(app: Any, form_options: dict[str, Any]) -> None:
    catalog = {item["value"]: item for item in form_options["strategies"]}

    @app.callback(
        Output("multi-channel-rows", "children"),
        Input("multi-channel-rows-store", "data"), Input("multi-risk-mode", "value"),
    )
    def render_rows(rows: list[dict[str, Any]] | None, risk_mode: str):
        return multi_views.channel_rows(list(rows or []), form_options, risk_mode)

    @app.callback(
        Output({"type": "multi-channel-timeframe", "index": MATCH}, "data"),
        Output({"type": "multi-channel-timeframe", "index": MATCH}, "value"),
        Input({"type": "multi-channel-strategy", "index": MATCH}, "value"),
        State({"type": "multi-channel-timeframe", "index": MATCH}, "value"),
    )
    def update_row_timeframes(strategy: str, current: str | None):
        frames = catalog.get(strategy, form_options["strategies"][0])["timeframes"]
        value = current if current in frames else frames[0]
        return [{"label": frame.upper(), "value": frame} for frame in frames], value

    @app.callback(
        Output("multi-channel-rows-store", "data"),
        Input("multi-add-channel", "n_clicks"),
        Input({"type": "multi-remove-channel", "index": ALL}, "n_clicks"),
        Input("multi-risk-mode", "value"), Input("multi-total-risk-limit", "value"),
        State("multi-channel-rows-store", "data"),
        State({"type": "multi-channel-strategy", "index": ALL}, "value"),
        State({"type": "multi-channel-symbol", "index": ALL}, "value"),
        State({"type": "multi-channel-timeframe", "index": ALL}, "value"),
        State({"type": "multi-channel-risk", "index": ALL}, "value"),
        State({"type": "multi-channel-source", "index": ALL}, "value"),
        prevent_initial_call=True,
    )
    def change_rows(_add: int | None, _remove: list[int], risk_mode, total_risk,
                    stored, strategies, symbols, timeframes, risks, sources):
        rows = _form_rows(stored, strategies, symbols, timeframes, risks, sources)
        triggered = ctx.triggered_id
        if triggered == "multi-add-channel":
            next_id = max((int(row["id"]) for row in rows), default=-1) + 1
            rows.append(multi_views.default_channel(form_options, next_id))
        elif isinstance(triggered, dict) and triggered.get("type") == "multi-remove-channel" and len(rows) > 1:
            target = int(triggered["index"])
            rows = [row for row in rows if int(row["id"]) != target]
        if risk_mode == actions.MULTI_CHANNEL_RISK_EQUAL:
            try:
                rows = actions.allocate_multi_channel_risk(
                    rows, mode=risk_mode, total_percent=total_risk,
                )["channels"]
            except ValueError:
                return no_update
        return rows

    @app.callback(
        Output("multi-risk-summary", "children"),
        Input("multi-risk-mode", "value"), Input("multi-total-risk-limit", "value"),
        Input({"type": "multi-channel-risk", "index": ALL}, "value"),
        State("multi-channel-rows-store", "data"),
    )
    def risk_summary(risk_mode, total_risk, risks, stored):
        try:
            rows = [dict(row) for row in (stored or [])]
            for index, risk in enumerate(risks or []):
                if index < len(rows):
                    rows[index]["risk_percent"] = risk
            allocation = actions.allocate_multi_channel_risk(
                rows, mode=risk_mode, total_percent=total_risk,
            )
            mode_label = "Equal split" if risk_mode == actions.MULTI_CHANNEL_RISK_EQUAL else "Manual"
            each = allocation["risk_per_channel_percent"]
            each_text = f"; {each}% per channel" if each is not None else ""
            return dmc.Alert(
                f"{mode_label}: {allocation['risk_used_percent']}% of "
                f"{allocation['risk_allocation']['total_percent']}% used across "
                f"{allocation['risk_allocation']['channel_count']} channels{each_text}.",
                color="blue", variant="light",
            )
        except Exception as exc:
            return dmc.Alert(str(exc), title="Invalid risk allocation", color="red")

    @app.callback(
        Output("multi-start-input", "disabled"), Output("multi-end-input", "disabled"),
        Input("multi-custom-period", "checked"),
    )
    def toggle_custom_period(enabled: bool):
        return not bool(enabled), not bool(enabled)

    @app.callback(
        Output("multi-channel-preview", "children"), Output("multi-history-status", "children"),
        Input({"type": "multi-channel-strategy", "index": ALL}, "value"),
        Input({"type": "multi-channel-symbol", "index": ALL}, "value"),
        Input({"type": "multi-channel-timeframe", "index": ALL}, "value"),
        Input({"type": "multi-channel-risk", "index": ALL}, "value"),
        Input({"type": "multi-channel-source", "index": ALL}, "value"),
        Input("global-signal-profile", "value"),
        State("multi-channel-rows-store", "data"),
    )
    def preview(strategies, symbols, timeframes, risks, sources, redis_profile, stored):
        rows = _form_rows(stored, strategies, symbols, timeframes, risks, sources)
        bounds = data_access.multi_channel_rows_date_bounds(rows, redis_profile=redis_profile)
        preview_view = multi_views.channel_preview(bounds, form_options)
        if not bounds.get("available"):
            return preview_view, dmc.Alert(bounds.get("error"), color="red")
        return preview_view, dmc.Text(
            f"Shared Redis range: {bounds['earliest_date']} to {bounds['latest_date']}", size="sm", c="dimmed",
        )

    @app.callback(
        Output("multi-selection-request", "data"), Output("multi-create-message", "children"),
        Input("multi-create-btn", "n_clicks"),
        State({"type": "multi-channel-strategy", "index": ALL}, "value"),
        State({"type": "multi-channel-symbol", "index": ALL}, "value"),
        State({"type": "multi-channel-timeframe", "index": ALL}, "value"),
        State({"type": "multi-channel-risk", "index": ALL}, "value"),
        State({"type": "multi-channel-source", "index": ALL}, "value"),
        State("multi-channel-rows-store", "data"),
        State("multi-risk-mode", "value"), State("multi-total-risk-limit", "value"),
        State("multi-custom-period", "checked"),
        State("multi-start-input", "value"), State("multi-end-input", "value"),
        State("multi-balance-input", "value"), State("multi-ksl-input", "value"),
        State("multi-ktp-input", "value"), State("multi-leg-count-input", "value"),
        State("multi-leg2-ktp-input", "value"), State("multi-leg3-ktp-input", "value"),
        State("multi-enable-daily-loss", "checked"), State("multi-max-daily-loss", "value"),
        State("multi-enable-max-drawdown", "checked"), State("multi-max-total-drawdown", "value"),
        State("multi-enable-consecutive-losses", "checked"), State("multi-max-consecutive-losses", "value"),
        State("global-signal-profile", "value"), prevent_initial_call=True,
    )
    def create_session(_clicks, strategies, symbols, timeframes, risks, sources, stored,
                       risk_mode, total_risk, custom_period,
                       start, end, balance, ksl, ktp, leg_count, leg2, leg3,
                       daily_enabled, daily_limit, dd_enabled, dd_limit,
                       consecutive_enabled, consecutive_limit, redis_profile):
        try:
            selected_source = session_store.confirmed_signal_profile(redis_profile)
            rows = _form_rows(stored, strategies, symbols, timeframes, risks, sources)
            bounds = data_access.multi_channel_rows_date_bounds(rows, redis_profile=selected_source)
            if not bounds.get("available"):
                raise ValueError(bounds.get("error"))
            if custom_period:
                if not start or not end:
                    raise ValueError("A custom range needs both a start date and an end date")
                if date.fromisoformat(str(start)) < date.fromisoformat(bounds["earliest_date"]):
                    raise ValueError(f"Start date is earlier than the shared Redis data: {bounds['earliest_date']}")
                if date.fromisoformat(str(end)) > date.fromisoformat(bounds["latest_date"]):
                    raise ValueError(f"End date is later than the shared Redis data: {bounds['latest_date']}")
            plan = actions.prepare_multi_channel_session(
                channels=rows, custom_period=bool(custom_period), start=start, end=end,
                balance=float(balance), ksl_level=ksl, ktp_level=ktp, leg_count=leg_count,
                leg2_ktp=leg2, leg3_ktp=leg3,
                enable_daily_loss_limit=bool(daily_enabled), max_daily_loss_percent=float(daily_limit),
                enable_max_drawdown=bool(dd_enabled), max_total_drawdown_percent=float(dd_limit),
                enable_max_consecutive_losses=bool(consecutive_enabled),
                max_consecutive_losses=consecutive_limit, redis_profile=selected_source,
                risk_allocation_mode=risk_mode, total_risk_percent=total_risk,
            )
            session = session_store.create_multi_channel_backtest(plan)
            preflight = plan["preflight"]
            return {"id": session["session_id"]}, dmc.Alert(
                f"Draft created: {preflight['channel_count']} channels × {preflight['period_count']} periods; "
                f"{preflight['new_count']} new runs. cTrader has not been run.", color="green",
            )
        except Exception as exc:
            return no_update, dmc.Alert(str(exc), title="Could not create session", color="red")

    @app.callback(
        Output("multi-session-select", "data"), Output("multi-session-select", "value"),
        Input("multi-refresh-btn", "n_clicks"), Input("multi-selection-request", "data"),
        Input("multi-delete-message", "children"), State("multi-session-select", "value"),
    )
    def refresh_options(_clicks, request, _delete, current):
        sessions = session_store.list_sessions(kind="multi_channel_backtest")
        valid = {item["session_id"] for item in sessions}
        requested = str((request or {}).get("id") or "") if ctx.triggered_id == "multi-selection-request" else str(current or "")
        selected = requested if requested in valid else None
        if selected is None:
            active = next((item for item in sessions if item["state"] == "running"), None)
            selected = active["session_id"] if active else None
        return multi_views.select_data(sessions), selected

    @app.callback(
        Output("multi-session-detail", "children"), Input("multi-refresh-btn", "n_clicks"),
        Input("multi-session-select", "value"), Input("multi-progress-poll", "n_intervals"),
    )
    def refresh_detail(_clicks, session_id, _poll):
        session = session_store.get(session_id)
        if session and session.get("kind") != "multi_channel_backtest":
            session = None
        if ctx.triggered_id == "multi-progress-poll" and (not session or session.get("state") == "running"):
            return no_update
        execution = data_access.read_multi_channel_backtest(session["plan"]) if session else None
        events = session_store.events(session["session_id"]) if session else []
        return multi_views.detail(session, execution, events, form_options)

    @app.callback(
        Output("multi-period-contribution", "children"),
        Input("multi-period-select", "value"), State("multi-period-data", "data"),
        prevent_initial_call=True,
    )
    def select_period(index: str | None, periods: list[dict[str, Any]] | None):
        rows = list(periods or [])
        try:
            period = rows[int(index or 0)]
        except (IndexError, TypeError, ValueError):
            period = None
        return multi_views.contribution_detail(period)

    @app.callback(
        Output("multi-progress-bar", "value"), Output("multi-progress-label", "children"),
        Output("multi-stop-btn", "disabled"), Output("multi-delete-btn", "disabled"),
        Output("multi-progress-poll", "disabled"),
        Input("multi-progress-poll", "n_intervals"), Input("multi-refresh-btn", "n_clicks"),
        Input("multi-session-select", "value"), Input("multi-run-confirm", "submit_n_clicks"),
        Input("multi-run-state", "data"),
    )
    def refresh_status(_poll, _refresh, session_id, _run, run_state):
        session = session_store.get(session_id)
        if not session or session.get("kind") != "multi_channel_backtest":
            return 0, "No Multi-channel session selected", True, True, True
        active = session["state"] == "running"
        progress = float(session.get("progress_pct") or 0)
        start_requested = bool((run_state or {}).get("active"))
        text = str(session.get("progress_text") or session["state"].title())
        keep_polling = active or start_requested
        return progress, single_views.format_progress(progress, text), not active, active, poll_disabled(keep_polling, ctx.triggered_id == "multi-progress-poll")

    @app.callback(
        Output("multi-run-message", "children"), Input("multi-run-confirm", "submit_n_clicks"),
        State("multi-session-select", "value"), background=True,
        running=[(Output("multi-run-btn", "disabled"), True, False), (Output("multi-run-state", "data"), {"active": True}, {"active": False})],
        prevent_initial_call=True,
    )
    def run_session(_clicks, session_id):
        if not session_id:
            return dmc.Alert("Select a session before running.", color="red")
        claimed = False
        try:
            pending = session_store.get(session_id)
            if not pending or pending.get("kind") != "multi_channel_backtest":
                raise ValueError("The session no longer exists")
            if pending["state"] not in {"draft", "failed", "cancelled"}:
                raise ValueError("Completed/cached sessions are view-only")
            actions.validate_multi_channel_runtime(pending["plan"])
            session = session_store.claim_run(session_id)
            claimed = True
            result = actions.run_multi_channel_session(
                session["plan"],
                on_progress=lambda percent, detail: session_store.update_progress(session_id, percent, detail),
                should_cancel=lambda: session_store.is_cancel_requested(session_id),
            )
            finished = session_store.complete(session_id, result)
            if finished["state"] == "cancelled":
                return dmc.Alert("Stopped safely.", color="orange")
            if finished["state"] == "failed":
                return dmc.Alert(finished["progress_text"], color="red")
            return dmc.Alert("Existing results were reused." if finished["state"] == "cached" else "Multi-channel Backtest completed.", color="teal" if finished["state"] == "cached" else "green")
        except Exception as exc:
            if claimed:
                session_store.fail(session_id, exc)
            return dmc.Alert(str(exc), title="MULTI-CHANNEL RUN FAILED", color="red")

    @app.callback(Output("multi-stop-message", "children"), Input("multi-stop-btn", "n_clicks"), State("multi-session-select", "value"), prevent_initial_call=True)
    def stop_session(_clicks, session_id):
        try:
            session_store.request_cancel(str(session_id))
            return dmc.Alert("Stop requested; the run will stop at the next safety poll.", color="orange")
        except Exception as exc:
            return dmc.Alert(str(exc), color="red")

    @app.callback(Output("multi-delete-message", "children"), Input("multi-delete-btn", "n_clicks"), State("multi-session-select", "value"), prevent_initial_call=True)
    def delete_session(_clicks, session_id):
        try:
            session_store.delete_sessions([str(session_id)])
            return dmc.Alert("Dashboard session deleted.", color="green")
        except Exception as exc:
            return dmc.Alert(str(exc), color="red")

    @app.callback(
        Output("multi-create-message", "children", allow_duplicate=True),
        Output("multi-run-message", "children", allow_duplicate=True),
        Output("multi-stop-message", "children", allow_duplicate=True),
        Output("multi-delete-message", "children", allow_duplicate=True),
        Input("multi-refresh-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_old_messages(_clicks):
        return None, None, None, None

    @app.callback(
        Output("multi-session-bot-activity-table", "data"),
        Output("multi-session-bot-activity-table", "page_count"),
        Output("multi-session-bot-activity-table", "page_current"),
        Input("multi-session-bot-activity-table", "page_current"),
        Input("multi-session-bot-activity-table", "sort_by"),
        Input("multi-session-bot-activity-table", "filter_query"),
        State("multi-session-bot-activity-table", "page_size"),
        State("multi-session-bot-activity-source", "data"),
        prevent_initial_call=True,
    )
    def page_legacy_activity(page_current, sort_by, filter_query, page_size, source):
        events = data_access.single_bot_events(str((source or {}).get("experiment") or ""))
        rows = single_callbacks._sorted_rows(
            single_callbacks._filtered_rows(single_views.bot_activity_rows(events), filter_query),
            sort_by,
        )
        size = max(1, int(page_size or single_views.BOT_ACTIVITY_PAGE_SIZE))
        page_count = max(1, math.ceil(len(rows) / size))
        triggered = set(ctx.triggered_prop_ids or {})
        restart = any(key.endswith((".sort_by", ".filter_query")) for key in triggered)
        page = 0 if restart else min(max(0, int(page_current or 0)), page_count - 1)
        return rows[page * size:(page + 1) * size], page_count, page
