"""Runtime logging setup shared by historical and live engines."""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import time
from datetime import datetime

from core_engine.logkit.formatters import operation_line
from core_engine.settings import LOG_LEVEL


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


_CONSOLE_SANITIZE_MAP = {
    "Ã¢Å“â€œ": "[OK]",
    "[OK]": "[OK]",
    "Ã¢Å“-": "[ERR]",
    "âœ—": "[ERR]",
    "Ã¢â€ Â»": "[RETRY]",
    "â†»": "[RETRY]",
    "Ã¢â€ â€™": "->",
    "->": "->",
    "Ã¢â‚¬â€": "-",
    "-": "-",
    "Ã¢â€°Ë†": "~",
    "â‰ˆ": "~",
    "Ã¢â‚¬Â¢": "-",
    "â€¢": "-",
    "Ã¢-â€¹": "[SKIP]",
    "[SKIP]": "[SKIP]",
    "Ã°Å¸â€Â": "[CHECK]",
    "ðŸ”": "[CHECK]",
    "Ã°Å¸â€Â§": "[FIX]",
    "ðŸ”§": "[FIX]",
    "Ã¢ÂÅ’": "[FAIL]",
    "[FAIL]": "[FAIL]",
    "Ã¢Å¡Â Ã¯Â¸Â": "[WARN]",
    "[WARN]": "[WARN]",
}


class ConsoleSanitizingFormatter(logging.Formatter):
    """Console-only formatter that normalizes mojibake/emoji markers."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for src, dst in _CONSOLE_SANITIZE_MAP.items():
            text = text.replace(src, dst)
        return text


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


def setup_logger(
    name: str,
    log_file: str,
    rotating: bool = False,
    *,
    console: bool = True,
    utc: bool = False,
    pipe_format: bool = False,
    normalize_prefixes: bool = False,
) -> logging.Logger:
    """Create a console + file logger for a runtime component."""
    logger = logging.getLogger(name)
    if logger.handlers:
        if normalize_prefixes and not any(isinstance(f, OperatorPrefixFilter) for f in logger.filters):
            logger.addFilter(OperatorPrefixFilter())
        return logger
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
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
    return logger
