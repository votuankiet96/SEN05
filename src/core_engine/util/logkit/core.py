"""Public logging API: routing, levels, context, and structured event emission."""

from __future__ import annotations

import contextvars
import logging
import os
import re
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from core_engine.settings import LOGGING, env_str
from core_engine.util.logkit.formatter import EventText, OperatorFormatter, clean, operation_line
from core_engine.util.logkit.sink import (
    SinkQueueHandler,
    flush_logs,
    process_role,
    sink_manager,
)

_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "dp_log_context",
    default={},
)
_LOGGERS: dict[str, logging.Logger] = {}


def _component_level(name: str) -> int:
    override = "LOG_LEVEL_" + re.sub(r"[^A-Za-z0-9]+", "_", name.upper()).strip("_")
    value = env_str(override) or LOGGING.level
    return getattr(logging, str(value).upper(), logging.INFO)


def _default_stream(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    role = process_role()
    if normalized in {
        "activity",
        "backend",
        "chart_datacheck",
        "process_control",
        "supervisor",
        "system",
    }:
        return "system"
    if normalized in {"alerts", "critical_outbox", "discord", "notification"}:
        return "alerts"
    if normalized.startswith("live") or normalized in {"candle_snapshot", "redis_snapshot"}:
        return "live"
    if normalized.startswith("historical"):
        return "historical"
    if role == "live":
        return "live"
    if role == "historical":
        return "historical"
    return "system"


class _ContextFilter(logging.Filter):
    def __init__(self, component: str, stream: str) -> None:
        super().__init__()
        self.component = component
        self.stream = stream

    def filter(self, record: logging.LogRecord) -> bool:
        record.dp_component = self.component
        record.dp_stream = self.stream
        record.dp_role = process_role()
        record.dp_context = dict(_CONTEXT.get())
        if not hasattr(record, "dp_fields"):
            record.dp_fields = {}
        return True


class _PrefixFilter(logging.Filter):
    _PREFIX = re.compile(r"^\[(?P<tag>[A-Za-z0-9_ -]{2,20})\]\s*(?P<text>.*)$")
    _AREA = {
        "AUTH": "AUTH",
        "AUDIT": "AUDIT",
        "CANCEL": "HISTORICAL",
        "DB": "DATABASE",
        "ERROR": "SYSTEM",
        "ERR": "SYSTEM",
        "OK": "SYSTEM",
        "SCHED": "SCHEDULER",
        "SHUTDOWN": "LIVE",
        "SPOOL": "BUFFER",
        "STARTUP": "LIVE",
        "WARN": "SYSTEM",
        "WARNING": "SYSTEM",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, EventText):
            return True
        try:
            raw = record.getMessage()
        except Exception:
            raw = str(record.msg)
        match = self._PREFIX.match(raw)
        if not match:
            return True
        tag = " ".join(match.group("tag").split()).upper()
        area = self._AREA.get(tag)
        text = match.group("text").strip() or tag.title()
        if area is None and re.fullmatch(r"G\d+", tag):
            area = "LIVE"
            text = f"Connection group {tag[1:]} {text}"
        if area:
            record.msg = operation_line(area, text)
            record.args = ()
        return True


def _console_enabled(requested: bool) -> bool:
    disabled = str(os.environ.get("DP_DISABLE_CONSOLE_LOG") or "").strip().lower()
    return requested and disabled not in {"1", "true", "yes"}


def _ensure_console_handler(logger: logging.Logger) -> None:
    if any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, SinkQueueHandler)
        for handler in logger.handlers
    ):
        return
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(OperatorFormatter())
    logger.addHandler(handler)


def get_logger(
    name: str,
    *,
    stream: str | None = None,
    console: bool = True,
    normalize_prefixes: bool = False,
) -> logging.Logger:
    """Return the only supported logger shape for production components."""
    selected_stream = str(stream or _default_stream(name)).lower()
    if selected_stream not in {"live", "historical", "system", "alerts"}:
        selected_stream = "system"
    key = f"{name}:{selected_stream}"
    existing = _LOGGERS.get(key)
    if existing is not None:
        if normalize_prefixes and not any(
            isinstance(item, _PrefixFilter) for item in existing.filters
        ):
            existing.addFilter(_PrefixFilter())
        if _console_enabled(console):
            _ensure_console_handler(existing)
        return existing

    logger = logging.getLogger(f"dp.{selected_stream}.{name}")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(_component_level(name))
    logger.propagate = False
    logger.addFilter(_ContextFilter(str(name), selected_stream))
    if normalize_prefixes:
        logger.addFilter(_PrefixFilter())
    logger.addHandler(
        SinkQueueHandler(
            sink_manager(),
            stream=selected_stream,
            component=str(name),
        )
    )

    if _console_enabled(console):
        _ensure_console_handler(logger)

    _LOGGERS[key] = logger
    return logger


@contextmanager
def bind_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Bind correlation fields for all events emitted in the current context."""
    current = dict(_CONTEXT.get())
    supplied_keys = {
        key
        for key in ("batch_id", "run_id", "job_id")
        if fields.get(key) not in (None, "")
    }
    if supplied_keys:
        for key in {"batch_id", "run_id", "job_id"} - supplied_keys:
            current.pop(key, None)
    for key, value in fields.items():
        if value is not None and value != "":
            current[str(key)] = value
    explicit_reference = fields.get("correlation_id")
    supplied_identity = fields.get("batch_id") or fields.get("run_id") or fields.get("job_id")
    if explicit_reference not in (None, ""):
        current["correlation_id"] = explicit_reference
    elif supplied_identity not in (None, ""):
        current["correlation_id"] = supplied_identity
    elif not current.get("correlation_id"):
        current["correlation_id"] = (
            current.get("batch_id")
            or current.get("run_id")
            or current.get("job_id")
        )
    token = _CONTEXT.set(current)
    try:
        yield current
    finally:
        _CONTEXT.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_CONTEXT.get())


def set_context(**fields: Any) -> dict[str, Any]:
    """Set process/thread-lifetime context; later calls may replace its fields."""
    current = dict(_CONTEXT.get())
    supplied_keys = {
        key
        for key in ("batch_id", "run_id", "job_id")
        if fields.get(key) not in (None, "")
    }
    if supplied_keys:
        for key in {"batch_id", "run_id", "job_id"} - supplied_keys:
            current.pop(key, None)
    for key, value in fields.items():
        if value is not None and value != "":
            current[str(key)] = value
    explicit_reference = fields.get("correlation_id")
    supplied_identity = fields.get("batch_id") or fields.get("run_id") or fields.get("job_id")
    if explicit_reference not in (None, ""):
        current["correlation_id"] = explicit_reference
    elif supplied_identity not in (None, ""):
        current["correlation_id"] = supplied_identity
    elif not current.get("correlation_id"):
        current["correlation_id"] = (
            current.get("batch_id")
            or current.get("run_id")
            or current.get("job_id")
        )
    _CONTEXT.set(current)
    return dict(current)


def log_event(
    logger: logging.Logger,
    level: int,
    event_code: str,
    message: str,
    *,
    area: str | None = None,
    stage: str | None = None,
    result: str | None = None,
    skip_notify: bool = False,
    exc_info: Any = None,
    **fields: Any,
) -> None:
    """Emit a catalog-ready event without exposing logging internals."""
    payload = dict(fields)
    payload["event_code"] = event_code
    if stage:
        payload["stage"] = stage
    if result:
        payload["result"] = result
    extra = {
        "dp_fields": payload,
        "dp_skip_notify": bool(skip_notify),
    }
    logger.log(
        level,
        operation_line(area or getattr(logger, "name", "SYSTEM").split(".")[1], message),
        extra=extra,
        exc_info=exc_info,
    )


def log_activity(
    action: str,
    *,
    component: str,
    status: str,
    message: str,
    **fields: Any,
) -> None:
    """Record one concise lifecycle action in ``system.log``."""
    value = str(status or "").strip().lower()
    level = logging.INFO
    if value in {"warning", "warn", "stopping", "cancelled", "skipped"}:
        level = logging.WARNING
    elif value in {"failed", "fail", "error", "crashed"}:
        level = logging.ERROR
    logger = get_logger("activity", stream="system", console=False)
    log_event(
        logger,
        level,
        f"{component}.{action}",
        clean(message),
        area=clean(component).upper(),
        result=value or "ok",
        **fields,
    )


__all__ = [
    "bind_context",
    "current_context",
    "flush_logs",
    "get_logger",
    "log_activity",
    "log_event",
    "set_context",
]
