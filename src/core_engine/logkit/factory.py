"""Component logger factory shared across DP Program.

`get_logger(component, log_file, ...)` is the single entry point every
component (live, historical, supervisor, auth, warehouse, dashboard,
activity...) should use to obtain its logger. In addition to the
console/file handlers a component asks for, every logger built here also
gets:

- The shared WARNING+ aggregate handler, so any warning/error/critical
  line from any component also lands in
  `runtime/logs/system/errors.log` (see `core_engine.logkit.handlers`).
- The shared CRITICAL handler, which turns a CRITICAL log record into an
  immediate Discord alert without the caller having to remember to send
  one.

Level policy (applies to every component):
- DEBUG: detailed diagnostics, off in production (`LOG_LEVEL=DEBUG` to see).
- INFO: normal operation (batch done, job start/finish, rows loaded).
- WARNING: degraded but self-recovering (retry, cooldown, stale data).
- ERROR: a task/component failed and needs attention.
- CRITICAL: service-level failure or data-loss risk; auto-alerts Discord.

The global level comes from `LOG_LEVEL`; an individual component can be
overridden with `LOG_LEVEL_<COMPONENT>` (component name upper-cased, e.g.
`LOG_LEVEL_LIVE_FETCHING=DEBUG`).
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time

from core_engine.logkit.formatters import ConsoleSanitizingFormatter, operation_line
from core_engine.logkit.handlers import (
    ResilientRotatingFileHandler,
    critical_discord_handler,
    errors_aggregate_handler,
)
from core_engine.settings import LOGGING, env_str


class OperatorPrefixFilter(logging.Filter):
    """Normalize old bracket-prefixed messages into operator log lines."""

    _PREFIX_RE = re.compile(r"^\[(?P<tag>[A-Z0-9_ ]{2,16})\]\s*(?P<text>.*)$")
    _AREA_BY_TAG = {
        "AUTH": "AUTH",
        "OK": "SYSTEM",
        "ERROR": "SYSTEM",
        "ERR": "SYSTEM",
        "WARN": "SYSTEM",
        "WARNING": "SYSTEM",
        "CANCEL": "HISTORICAL",
        "DB": "DATABASE",
        "SPOOL": "BUFFER",
        "SCHED": "SCHEDULER",
        "AUDIT": "AUDIT",
        "STARTUP": "LIVE",
        "SHUTDOWN": "LIVE",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        raw = str(record.msg)
        match = self._PREFIX_RE.match(raw)
        if not match:
            return True
        tag = " ".join(match.group("tag").split()).upper()
        area = self._AREA_BY_TAG.get(tag)
        text = match.group("text").strip() or tag.title()
        if area is None and re.fullmatch(r"G\d+", tag):
            area = "LIVE"
            text = f"Group {tag[1:]} {text}"
        if not area:
            return True
        record.msg = operation_line(area, text)
        return True


def _component_level(name: str) -> int:
    override_var = "LOG_LEVEL_" + re.sub(r"[^A-Za-z0-9]+", "_", name.upper()).strip("_")
    level_name = env_str(override_var) or LOGGING.level
    return getattr(logging, level_name.upper(), logging.INFO)


def get_logger(
    name: str,
    log_file: str,
    rotating: bool = False,
    *,
    console: bool = True,
    utc: bool = False,
    pipe_format: bool = False,
    normalize_prefixes: bool = False,
) -> logging.Logger:
    """Create (or return the already-built) logger for a runtime component."""
    logger = logging.getLogger(name)
    if logger.handlers:
        if normalize_prefixes and not any(isinstance(f, OperatorPrefixFilter) for f in logger.filters):
            logger.addFilter(OperatorPrefixFilter())
        return logger
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.setLevel(_component_level(name))
    logger.propagate = False
    if normalize_prefixes:
        logger.addFilter(OperatorPrefixFilter())

    log_dir = os.path.dirname(os.path.abspath(log_file))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    console_fmt = ConsoleSanitizingFormatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    file_fmt_text = "%(asctime)s | %(levelname)-7s | %(message)s" if pipe_format else "%(asctime)s [%(levelname)s] %(message)s"
    file_fmt = logging.Formatter(
        file_fmt_text,
        datefmt="%Y-%m-%d %H:%M:%S UTC" if utc else "%Y-%m-%d %H:%M:%S",
    )
    if utc:
        console_fmt.converter = time.gmtime
        file_fmt.converter = time.gmtime

    disable_console = str(os.environ.get("DP_DISABLE_CONSOLE_LOG") or "").strip().lower() in {"1", "true", "yes"}
    if console and not disable_console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(console_fmt)
        logger.addHandler(sh)
    if rotating:
        fh = ResilientRotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    else:
        fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(file_fmt)
    logger.addHandler(fh)

    logger.addHandler(errors_aggregate_handler())
    logger.addHandler(critical_discord_handler())
    return logger
