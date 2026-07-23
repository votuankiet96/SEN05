"""Canonical DP Program log schema and operator-facing rendering.

Every persisted record is one physical line with seven human-readable
columns followed by one compact JSON object.  Operators can read the columns
directly; automated reviewers should consume the JSON payload instead of
inferring meaning from prose.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
_PIPE = re.compile(r"\s*\|\s*")
_SPACE = re.compile(r"\s+")
_KEY = re.compile(r"[^a-z0-9_]+")
_EVENT = re.compile(r"[^a-z0-9]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(auth[_-]?token|access[_-]?token|cookie|password|secret|webhook)"
    r"\s*[:=]\s*([^,\s|;]+)"
)
_WEBHOOK_URL = re.compile(
    r"https://(?:discord(?:app)?\.com|discord\.com)/api/webhooks/\S+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SAFE_SECRET_STATES = {
    "authenticated",
    "configured",
    "guest",
    "missing",
    "no",
    "not-configured",
    "present",
    "unchanged",
    "updated",
    "yes",
}


def _redact_text(value: Any) -> str:
    text = str(value)
    text = _WEBHOOK_URL.sub("<discord_webhook_redacted>", text)
    text = _BEARER.sub("Bearer <redacted>", text)

    def replace_assignment(match: re.Match[str]) -> str:
        state = match.group(2).strip().lower()
        if state in _SAFE_SECRET_STATES:
            return match.group(0)
        return f"{match.group(1)}=<redacted>"

    return _SECRET_ASSIGNMENT.sub(replace_assignment, text)


def clean(value: Any) -> str:
    """Return one safe English/UTF-8 log cell without line or pipe breaks."""
    if value is None:
        return "-"
    text = _redact_text(value).replace("\r", " ").replace("\n", " ").replace("|", "/")
    return _SPACE.sub(" ", text).strip() or "-"


def num(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return clean(value)


def ts(value: Any) -> str:
    if value is None:
        return "-"
    try:
        if getattr(value, "tzinfo", None) is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return clean(value)


def window(start: Any, end: Any) -> str:
    if start is None and end is None:
        return "-"
    return f"from {ts(start)} to {ts(end)}"


def value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}" if abs(value) >= 1000 else str(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return clean(value)


def cell(value: Any, width: int, *, align: str = "left") -> str:
    text = "-" if value is None or value == "" else clean(value)
    if len(text) > width:
        text = text[: max(1, width - 3)].rstrip() + "..."
    if align == "right":
        return text.rjust(width)
    if align == "center":
        return text.center(width)
    return text.ljust(width)


def kv(label: str, value: Any, *, label_width: int = 17) -> str:
    return f"  {label:<{label_width}}: {clean(value)}"


def _safe_key(value: Any) -> str:
    return _KEY.sub("_", str(value).strip().lower()).strip("_")


def _event_code(area: str, event: str) -> str:
    left = _EVENT.sub(".", clean(area).lower()).strip(".") or "system"
    right = _EVENT.sub(".", clean(event).lower()).strip(".") or "event"
    return f"{left}.{right}"


class EventText(str):
    """String-compatible event carrying structured data into logging."""

    def __new__(
        cls,
        rendered: str,
        *,
        area: str,
        event: str,
        details: tuple[str, ...],
        fields: dict[str, Any],
    ) -> "EventText":
        obj = str.__new__(cls, rendered)
        obj.area = clean(area).upper()  # type: ignore[attr-defined]
        obj.event = clean(event)  # type: ignore[attr-defined]
        obj.details = details  # type: ignore[attr-defined]
        obj.fields = fields  # type: ignore[attr-defined]
        return obj


def operation_line(area: str, event: str, *details: str, **fields: Any) -> EventText:
    """Build one structured event while remaining compatible with ``str``."""
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        safe = _safe_key(key)
        if safe and value is not None and value != "":
            normalized[safe] = value
    normalized_details = tuple(
        text for text in (clean(item) for item in details) if text and text != "-"
    )
    rendered = " | ".join(
        [
            clean(area).upper(),
            clean(event),
            *normalized_details,
            *(f"{key}={value_text(value)}" for key, value in normalized.items()),
        ]
    )
    return EventText(
        rendered,
        area=area,
        event=event,
        details=normalized_details,
        fields=normalized,
    )


def _infer_stage(event: str, level: int, fields: dict[str, Any]) -> str:
    explicit = fields.get("stage")
    if explicit:
        return clean(explicit).upper()
    text = event.lower()
    if level >= logging.CRITICAL:
        return "CRITICAL"
    if any(word in text for word in ("failed", "failure", "error", "crash")):
        return "FAILED"
    if any(word in text for word in ("recover", "reconnect", "retry", "resume")):
        return "RECOVERY"
    if any(word in text for word in ("complete", "completed", "ready", "success", "succeeded")):
        return "COMPLETE"
    if any(word in text for word in ("start", "begin", "launch", "spawn")):
        return "START"
    if any(word in text for word in ("stop", "shutdown", "cancel")):
        return "STOP"
    if level >= logging.WARNING:
        return "ATTENTION"
    return "PROGRESS"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        return value.isoformat()
    except Exception:
        return clean(value)


def _safe_field(key: str, value: Any) -> Any:
    normalized = _safe_key(key)
    if any(word in normalized for word in ("password", "secret", "webhook")):
        return "<redacted>"
    if normalized in {"token", "auth_token", "access_token", "cookie"}:
        state = str(value).strip().lower()
        return value if state in _SAFE_SECRET_STATES else "<redacted>"
    return _json_safe(value)


def _result_text(fields: dict[str, Any], level: int) -> str:
    for key in ("result", "status", "outcome"):
        if fields.get(key) not in (None, ""):
            return clean(fields[key]).upper()
    if level >= logging.CRITICAL:
        return "ACTION REQUIRED"
    if level >= logging.ERROR:
        return "FAILED"
    if level >= logging.WARNING:
        return "MONITORING"
    return "OK"


def _reference(fields: dict[str, Any]) -> str:
    for key in ("correlation_id", "batch_id", "run_id", "job_id", "alert_id"):
        if fields.get(key) not in (None, ""):
            return clean(fields[key])
    return "-"


class OperatorFormatter(logging.Formatter):
    """Render a record for both direct reading and deterministic querying."""

    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        context = dict(getattr(record, "dp_context", {}) or {})
        component = clean(getattr(record, "dp_component", record.name)).lower()
        stream = clean(getattr(record, "dp_stream", "system")).lower()
        role = clean(getattr(record, "dp_role", "unknown")).lower()

        event_text: EventText | None = None
        if isinstance(record.msg, EventText) and not record.args:
            event_text = record.msg
        elif (
            record.msg == "%s"
            and isinstance(record.args, tuple)
            and len(record.args) == 1
            and isinstance(record.args[0], EventText)
        ):
            event_text = record.args[0]

        if event_text is not None:
            area = event_text.area
            event_name = event_text.event
            details = list(event_text.details)
            fields = dict(event_text.fields)
            human_message = event_name
            if details:
                human_message = f"{human_message}: {'; '.join(details)}"
        else:
            raw = clean(record.getMessage())
            parsed = _PIPE.split(raw)
            known_areas = {
                "ALERT",
                "AUDIT",
                "AUTH",
                "BUFFER",
                "CHART",
                "DATABASE",
                "HISTORICAL",
                "LIVE",
                "REDIS",
                "SCHEDULER",
                "SYSTEM",
            }
            if len(parsed) >= 2 and parsed[0].upper() in known_areas:
                area = parsed[0].upper()
                event_name = parsed[1]
                details = parsed[2:]
                human_message = event_name
                if details:
                    human_message = f"{human_message}: {'; '.join(details)}"
            else:
                area = clean(getattr(record, "dp_area", component)).upper()
                event_name = raw
                details = []
                human_message = raw
            fields = {}

        record_fields = dict(getattr(record, "dp_fields", {}) or {})
        fields.update(record_fields)
        fields = {**context, **fields}
        event_code = clean(fields.pop("event_code", "")).lower()
        if not event_code or event_code == "-":
            event_code = _event_code(area, event_name)

        exc_text = None
        if record.exc_info:
            exc_text = "".join(traceback.format_exception(*record.exc_info)).strip()
        elif record.exc_text:
            exc_text = str(record.exc_text)
        if exc_text:
            fields["exception"] = exc_text

        created = datetime.fromtimestamp(record.created, timezone.utc)
        timestamp = created.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        stage = _infer_stage(event_name, record.levelno, fields)
        result = _result_text(fields, record.levelno)
        reference = _reference(fields)
        correlation_id = fields.get("correlation_id")
        if not correlation_id:
            correlation_id = fields.get("batch_id") or fields.get("run_id") or fields.get("job_id")
            if correlation_id not in (None, ""):
                fields["correlation_id"] = correlation_id

        meta = {
            "schema": SCHEMA_VERSION,
            "event": event_code,
            "stream": stream,
            "component": component,
            "role": role,
            "pid": record.process,
            "thread": record.threadName,
            "event_id": clean(getattr(record, "dp_event_id", "-")),
            **{key: _safe_field(key, value) for key, value in fields.items()},
        }
        return " | ".join(
            [
                timestamp,
                f"{record.levelname:<8}",
                f"{area:<12}",
                f"{stage:<12}",
                clean(human_message),
                result,
                reference,
                json.dumps(meta, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            ]
        )


def parse_operator_line(line: str) -> dict[str, Any] | None:
    """Parse one canonical line. Return ``None`` for legacy/unstructured text."""
    parts = str(line).rstrip("\r\n").split(" | ", 7)
    if len(parts) != 8:
        return None
    try:
        meta = json.loads(parts[7])
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    parsed = dict(meta)
    parsed.update({
        "timestamp": parts[0].strip(),
        "level": parts[1].strip(),
        "area": parts[2].strip(),
        "stage": parts[3].strip(),
        "message": parts[4].strip(),
        "result": parts[5].strip(),
        "reference": parts[6].strip(),
    })
    return parsed


__all__ = [
    "EventText",
    "OperatorFormatter",
    "SCHEMA_VERSION",
    "cell",
    "clean",
    "kv",
    "num",
    "operation_line",
    "parse_operator_line",
    "ts",
    "value_text",
    "window",
]
