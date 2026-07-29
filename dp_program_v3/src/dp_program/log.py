"""Application-wide structured logging and secret-safe error text."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_EVENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")
_RISKS = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_key|auth_token|authorization|connection_string|"
    r"cookie|credential|password|pwd|secret|token|uid|username)(?:$|_)"
)
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|pwd|cookie|authorization|auth[_ -]?token|"
    r"api[_ -]?key|secret|uid|user(?:name|[ _-]?id))\s*[:=]\s*"
    r"(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^,;\s]+)"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b")


def _redact_text(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ")
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return _JWT.sub("[REDACTED]", text)


def safe_error(error: BaseException, *, limit: int = 300) -> str:
    """Return one bounded, single-line exception description without credentials."""
    text = _redact_text(f"{type(error).__name__}: {error}")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _format_value(key: str, value: Any) -> str:
    if _SECRET_KEY.search(key):
        value = "[REDACTED]"
    elif isinstance(value, BaseException):
        value = safe_error(value)
    elif isinstance(value, datetime):
        value = value.isoformat()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    text = _redact_text(str(value))
    return text if _PLAIN.fullmatch(text) else json.dumps(text, ensure_ascii=True)


def log_event(
    logger: logging.Logger,
    level: int | str,
    event: str,
    risk: str,
    *,
    component: str,
    **fields: Any,
) -> None:
    """Write one stable key=value event suitable for humans and simple queries."""
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
    number = (
        getattr(logging, level.upper(), logging.INFO)
        if isinstance(level, str)
        else int(level)
    )
    values = {
        "component": component,
        "event": event,
        "risk": risk,
        "pid": os.getpid(),
        **fields,
    }
    logger.log(number, " ".join(f"{key}={_format_value(key, value)}" for key, value in values.items()))


def configure_logging(config: dict[str, Any]) -> None:
    """Configure console and bounded durable production logs once."""
    root = logging.getLogger()
    if getattr(root, "_dp_program_configured", False):
        return
    level = getattr(
        logging,
        str(config["app"].get("log_level", "INFO")).upper(),
        logging.INFO,
    )
    log_path = Path(config["app"]["runtime_dir"]) / "logs" / "dp_program.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=int(config["service"]["log_max_bytes"]),
        backupCount=int(config["service"]["log_backup_count"]),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)
    root._dp_program_configured = True
