"""Log handlers shared across DP Program components.

Includes the Windows-hardened rotating file handler, the WARNING+
cross-component aggregate handler (`runtime/logs/system/errors.log`), and
the handler that turns a CRITICAL log record into an immediate Discord
alert.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from datetime import datetime


class ResilientRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler variant that avoids stderr floods on Windows locks."""

    def __init__(self, *args, rollover_retry_sec: int = 300, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_retry_sec = rollover_retry_sec
        self._rollover_retry_at = 0.0
        self._rollover_warned = False
        self._primary_base_filename = self.baseFilename

    def shouldRollover(self, record):
        if self.maxBytes > 0 and time.monotonic() < self._rollover_retry_at:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as exc:
            if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) != 32:
                raise
            fallback = self._switch_to_fallback_log()
            if fallback:
                self._rollover_retry_at = 0.0
            else:
                self._rollover_retry_at = time.monotonic() + self._rollover_retry_sec
                self._reopen_after_rollover_failure()
            self._write_rollover_warning(exc, fallback)
        else:
            self._rollover_retry_at = 0.0
            self._rollover_warned = False

    def _switch_to_fallback_log(self) -> str | None:
        root, ext = os.path.splitext(self._primary_base_filename)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = f"{root}.active.{os.getpid()}.{stamp}{ext or '.log'}"
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        try:
            self.baseFilename = os.path.abspath(fallback)
            self.mode = "a"
            self.stream = self._open()
            return self.baseFilename
        except Exception:
            self.baseFilename = self._primary_base_filename
            self.stream = None
            return None

    def _reopen_after_rollover_failure(self) -> None:
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if not self.delay:
            try:
                self.stream = self._open()
            except Exception:
                self.stream = None

    def _write_rollover_warning(self, exc: OSError, fallback: str | None = None) -> None:
        if self._rollover_warned:
            return
        self._rollover_warned = True
        if fallback:
            action = f"Switched current log file to {fallback}."
        else:
            action = f"Retrying in {self._rollover_retry_sec}s."
        msg = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [WARNING] "
            f"Log rollover delayed for {self._primary_base_filename}: {exc}. "
            f"{action}\n"
        )
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(msg)
            self.flush()
        except Exception:
            pass


_ERRORS_AGGREGATE_HANDLER: logging.Handler | None = None


def errors_aggregate_handler() -> logging.Handler:
    """Return the process-wide WARNING+ handler writing to errors.log.

    Every component logger created by `core_engine.logkit.get_logger` gets
    this same handler instance attached, so any WARNING/ERROR/CRITICAL
    record from any component lands in one place
    (`runtime/logs/system/errors.log`) in addition to its own component log
    file, without changing what that component log file contains.
    """
    global _ERRORS_AGGREGATE_HANDLER
    if _ERRORS_AGGREGATE_HANDLER is not None:
        return _ERRORS_AGGREGATE_HANDLER

    from core_engine.settings import SYSTEM_LOG_DIR

    path = SYSTEM_LOG_DIR / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = ResilientRotatingFileHandler(
        str(path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.WARNING)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S UTC",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    _ERRORS_AGGREGATE_HANDLER = handler
    return handler


class CriticalDiscordHandler(logging.Handler):
    """Forward CRITICAL log records to Discord as an immediate alert.

    This lets any component report a CRITICAL condition (cannot start,
    forced to drop data, ...) simply by logging at CRITICAL level, instead
    of every call site having to remember to also call the Discord
    notifier. Delivery reuses the existing outbound Discord channel
    (webhook check, retry, circuit breaker, dedupe) - this handler only
    decides *when* to send, not *how*.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.CRITICAL)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Imported lazily: core_engine.reporting.discord itself logs
            # through this package, so importing it at module load time
            # would create a circular import.
            from core_engine.reporting.discord import send_alert

            send_alert("critical", f"[{record.name}] {record.getMessage()}")
        except Exception:
            pass


_CRITICAL_DISCORD_HANDLER: logging.Handler | None = None


def critical_discord_handler() -> logging.Handler:
    global _CRITICAL_DISCORD_HANDLER
    if _CRITICAL_DISCORD_HANDLER is None:
        _CRITICAL_DISCORD_HANDLER = CriticalDiscordHandler()
    return _CRITICAL_DISCORD_HANDLER
