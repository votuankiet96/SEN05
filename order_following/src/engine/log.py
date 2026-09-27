"""Structured, English, key=value logging shared by every OF module.

Modeled directly on dp_program's own log.py (same SEN05 system, VM-DP6) — same key=value format,
same event/risk/component validation, same secret redaction. Every module gets its own
`logging.getLogger(__name__)` and calls `log_event()` directly — no logger object is threaded
through function signatures.
"""

import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from typing import Any

_EVENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")
_RISKS = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
# Field co ten giong secret thi che toan bo gia tri, khong xet noi dung.
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:client_secret|client_id|bot_token|access_token|refresh_token|password|token|secret|chat_id)(?:$|_)"
)
# Che cac cum dang client_secret=... hoac password: ... lot vao trong text tu do (vd exception message).
_SECRET_VALUE = re.compile(
    r"(?i)\b(client_secret|client_id|bot_token|access_token|refresh_token|password|token|secret)\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^,;\s&]+)"
)


def _redact_text(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ")
    return _SECRET_VALUE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


def safe_error(error: BaseException, *, limit: int = 300) -> str:
    """Return one bounded, single-line exception description without credentials."""
    text = _redact_text(f"{type(error).__name__}: {error}")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _format_value(key: str, value: Any) -> str:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, BaseException):
        value = safe_error(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    text = _redact_text(str(value))
    return text if _PLAIN.fullmatch(text) else f'"{text}"'


def log_event(logger: logging.Logger, level, event: str, risk: str, *, component: str, **fields: Any) -> None:
    """Write one stable key=value event line: component=x event=Y risk=Z pid=N field=value..."""
    event = str(event).upper()
    risk = str(risk).upper()
    component = str(component).lower()
    if not _EVENT.fullmatch(event):
        raise ValueError(f"invalid log event: {event}")
    if risk not in _RISKS:
        raise ValueError(f"invalid log risk: {risk}")
    if not _FIELD.fullmatch(component):
        raise ValueError(f"invalid log component: {component}")
    invalid = [key for key in fields if not _FIELD.fullmatch(key)]
    if invalid:
        raise ValueError(f"invalid log fields: {', '.join(invalid)}")
    number = getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else int(level)
    values = {"component": component, "event": event, "risk": risk, "pid": os.getpid(), **fields}
    logger.log(number, " ".join(f"{key}={_format_value(key, value)}" for key, value in values.items()))


def configure_logging(log_dir: str, *, name: str = "of", level: str = "INFO") -> None:
    """Configure the ROOT logger once — every module's `logging.getLogger(__name__)` inherits this
    (propagation is Python logging's default), so nothing else needs to call this again."""
    root = logging.getLogger()
    if getattr(root, "_of_configured", False):
        return
    os.makedirs(log_dir, exist_ok=True)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime  # timestamp luon la UTC, ky hieu bang Z o cuoi
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, f"{name}.log"), maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(file_handler)
    root.addHandler(console)
    root._of_configured = True
