"""Operator activity timeline for DP Program lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from core_engine.logkit.factory import get_logger
from core_engine.settings import ACTIVITY_LOG


_logger = get_logger("activity", str(ACTIVITY_LOG), rotating=True, console=False)

_COMPONENT_LABELS = {
    "system": "System",
    "terminal": "Terminal",
    "live_fetching": "Live Fetching",
    "historical_pulling": "Historical Pulling",
    "database": "Database",
    "auth": "TradingView Login",
}

_FIELD_LABELS = {
    "active_owner": "Active owner",
    "active_pid": "Active PID",
    "active_processes": "Active processes",
    "active_started": "Active since",
    "command": "Command",
    "deleted_files": "Deleted files",
    "decision": "Decision",
    "duration_seconds": "Duration",
    "error": "Error",
    "exit_code": "Exit code",
    "forced_processes": "Forced stops",
    "historical_cancel_file": "Historical stop file",
    "historical_exit_code": "Historical exit",
    "historical_pid": "Historical PID",
    "job_id": "Job ID",
    "kind": "Process group",
    "live_exit_code": "Live exit",
    "live_enabled": "Live enabled",
    "live_pid": "Live PID",
    "live_signal_sent": "Live stop signal",
    "pid": "PID",
    "reason": "Reason",
    "requested": "Requested action",
    "requested_by": "Requested by",
    "schedule_enabled": "Schedule enabled",
    "stdout_log": "Output log",
    "title": "Title",
    "url": "URL",
}


def _level_for(status: str) -> int:
    value = str(status or "").strip().lower()
    if value in {"failed", "fail", "error", "crashed"}:
        return logging.ERROR
    if value in {"warning", "warn", "stopping", "cancelled", "skipped"}:
        return logging.WARNING
    return logging.INFO


def _clean(value: Any) -> str:
    text = str(value)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _label_component(value: str) -> str:
    raw = _clean(value)
    return _COMPONENT_LABELS.get(raw, raw.replace("_", " ").title())


def _label_field(value: str) -> str:
    raw = _clean(value)
    return _FIELD_LABELS.get(raw, raw.replace("_", " ").title())


def _format_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if key == "duration_seconds":
        try:
            seconds = float(value)
            if seconds >= 60:
                return f"{seconds / 60:.1f} min"
            return f"{seconds:.1f} sec"
        except Exception:
            return _clean(value)
    return _clean(value)


def log_activity(
    action: str,
    *,
    component: str,
    status: str,
    message: str,
    **fields: Any,
) -> None:
    """Write one concise operator-facing activity line.

    The activity log is intentionally not a raw debug stream. It answers:
    which part of DP Program acted, what happened, what the result was, and
    which IDs/counts help the operator trace it later.
    """
    component_text = _label_component(component)
    status_text = _clean(status).upper()
    action_text = _clean(action).replace("_", " ").title()
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(f"{_label_field(key)}: {_format_value(str(key), value)}")
    detail = " | ".join(parts)
    line = f"{component_text:<20} | {status_text:<10} | {action_text:<28} | {_clean(message)}"
    if detail:
        line = f"{line} | {detail}"
    _logger.log(_level_for(status), line)
