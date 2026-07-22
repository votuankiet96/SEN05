"""Discord webhook notifications for the refactored DP backend.

Public API: send_alert, flush_pending, notify_*_event, and
QUICK_COMMANDS_HINT. Discord webhooks are outbound-only.
"""

from __future__ import annotations

import logging
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from typing import Any
from uuid import uuid4

from core_engine.settings import DISCORD_LOG, LOGGING, NOTIFICATION, RUN_DIR
from core_engine.util.logkit.formatters import operation_line
from core_engine.util.logkit.handlers import ResilientRotatingFileHandler
from core_engine.util.logkit.paths import process_role, process_scoped_log_path, register_log_sink
from core_engine.util.notify.transport import post_webhook_once

logger = logging.getLogger(__name__)
_discord_logger_configured = False
_discord_logger_lock = threading.Lock()
_discord_circuit_lock = threading.Lock()
_discord_failure_count = 0
_discord_circuit_open_until = 0.0
_discord_dedupe_lock = threading.Lock()
_discord_last_sent: dict[str, float] = {}
_discord_suppressed: dict[str, int] = {}
_discord_last_dedupe_cleanup = 0.0
_discord_status_lock = threading.Lock()
_discord_last_success_at: str | None = None
_discord_last_failure_at: str | None = None
_discord_last_failure_error: str | None = None
_discord_logger_error: str | None = None
_discord_queue_rejections = 0

DISCORD_SEND_ATTEMPTS = NOTIFICATION.discord_send_attempts
DISCORD_TIMEOUT_CONNECT_SEC = NOTIFICATION.discord_timeout_connect_sec
DISCORD_TIMEOUT_READ_SEC = NOTIFICATION.discord_timeout_read_sec
DISCORD_CIRCUIT_FAILURES = NOTIFICATION.discord_circuit_failures
DISCORD_CIRCUIT_COOLDOWN_SEC = NOTIFICATION.discord_circuit_cooldown_sec
DISCORD_DEDUPE_WINDOW_SEC = NOTIFICATION.discord_dedupe_window_sec
DISCORD_QUEUE_MAXSIZE = 256
DISCORD_DEDUPE_MAX_KEYS = 4096
_UNSAFE_SSLKEYLOG_PREFIXES = ("\\\\.\\", "\\\\??\\")


@dataclass(frozen=True)
class _DiscordSendItem:
    payload: dict[str, Any]
    kind: str
    level: str
    meta: dict[str, Any]


class _DiscordSender:
    """One bounded, process-local worker for ordinary Discord messages.

    Ordinary reports are intentionally not the durable CRITICAL path (that
    path uses ``critical_outbox.py``).  A bounded queue prevents a webhook
    outage from creating one daemon thread per report and exhausting process
    resources.  The single worker also makes delivery-time deduplication
    deterministic: a failed message never causes the next queued copy to be
    suppressed.
    """

    _STOP = object()

    def __init__(self, *, maxsize: int = DISCORD_QUEUE_MAXSIZE) -> None:
        self._queue: queue.Queue[_DiscordSendItem | object] = queue.Queue(maxsize=maxsize)
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._pending_condition = threading.Condition()
        self._pending = 0

    def _ensure_worker(self) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            return
        with self._worker_lock:
            worker = self._worker
            if worker is not None and worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                daemon=True,
                name="discord-sender",
            )
            self._worker.start()

    def submit(self, item: _DiscordSendItem) -> bool:
        self._ensure_worker()
        # Keep the pending increment atomic with publication.  Otherwise the
        # worker can finish a very fast send before submit() increments it.
        with self._pending_condition:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                return False
            self._pending += 1
        return True

    def flush(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for all currently queued work."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._pending_condition:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._pending_condition.wait(timeout=remaining)
            return True

    def close(self, timeout: float = 1.0) -> bool:
        """Stop this sender instance (used by isolated tests)."""
        if not self.flush(timeout):
            return False
        worker = self._worker
        if worker is None:
            return True
        try:
            self._queue.put_nowait(self._STOP)
        except queue.Full:
            return False
        worker.join(timeout=max(0.0, timeout))
        return not worker.is_alive()

    def status(self) -> dict[str, Any]:
        with self._pending_condition:
            pending = self._pending
        worker = self._worker
        return {
            "queue_pending": pending,
            "queue_maxsize": self._queue.maxsize,
            "worker_started": worker is not None,
            "worker_alive": bool(worker and worker.is_alive()),
        }

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._STOP:
                    return
                assert isinstance(item, _DiscordSendItem)
                _deliver_item(item)
            except Exception as exc:
                # A malformed payload or an unexpected bookkeeping failure
                # must not kill the sole sender and strand the queue forever.
                _log_sender_exception("worker", exc)
            finally:
                self._queue.task_done()
                if item is not self._STOP:
                    with self._pending_condition:
                        self._pending -= 1
                        self._pending_condition.notify_all()
                    _persist_discord_status("queue_progress")


_discord_sender = _DiscordSender()

# Discord embed side-bar colors.
_COLORS = {
    "SUCCESS": 0x2ECC71,
    "OK": 0x2ECC71,
    "INFO": 0x2ECC71,
    "WARNING": 0xF1C40F,
    "WARN": 0xF1C40F,
    "ERROR": 0xE74C3C,
    "FAIL": 0xE74C3C,
    "FAILED": 0xE74C3C,
    "CRITICAL": 0xC0392B,
}

_LEVEL_TITLES = {
    "SUCCESS": "OK",
    "OK": "OK",
    "INFO": "Info",
    "WARNING": "Needs attention",
    "WARN": "Needs attention",
    "ERROR": "Action needed",
    "FAIL": "Action needed",
    "FAILED": "Action needed",
    "CRITICAL": "Critical",
}

_STATUS_BY_LEVEL = {
    "SUCCESS": "success",
    "OK": "success",
    "INFO": "success",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "failed",
    "FAIL": "failed",
    "FAILED": "failed",
    "CRITICAL": "failed",
}

_FEATURE_LABELS = {
    "backend": "DP Program supervisor",
    "live": "Live OHLCV feed",
    "auth": "TradingView login",
    "tradingview_auth": "TradingView login",
    "historical": "Historical pull",
    "database": "Database write",
    "config": "Configuration",
    "logs": "Logs",
    "system": "System",
}

_RESULT_LABELS = {
    "success": "Delivered",
    "warning": "Needs attention",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "stopping": "Stopping",
    "stopped": "Stopped",
    "started": "Started",
    "completed": "Completed",
    "healthy": "Healthy",
    "refreshing": "Refreshing",
    "notified": "Notified",
    "queued": "Queued",
}

_RESULT_ACTION_WORDS = {
    "success": "No action needed",
    "warning": "Check soon",
    "failed": "Fix now",
    "cancelled": "Review",
    "stopping": "Stopping",
    "stopped": "Confirm expected",
    "started": "Monitor",
    "completed": "Review numbers",
    "healthy": "No action needed",
    "refreshing": "No action needed",
    "notified": "Review",
    "queued": "Wait",
}

_OPERATOR_REPLACEMENTS = (
    (r"\bWS Live\b", "Live feed"),
    (r"\bWS live\b", "Live feed"),
    (r"\bws_live\b", "live feed"),
    (r"\bWS batch\b", "live batch"),
    (r"\bWS\b", "live connection"),
    (r"\bWebSocket\b", "live connection"),
    (r"\bwebsocket\b", "live connection"),
    (r"\bDB/ETL\b", "database write"),
    (r"\bETL direct\b", "warehouse save"),
    (r"\bETL\b", "warehouse save"),
    (r"\bDWH\.Fact_OHLCV\b", "main OHLCV table"),
    (r"\bFact_OHLCV\b", "main OHLCV table"),
    (r"\bFact rows\b", "saved rows"),
    (r"\bFact row\b", "saved row"),
    (r"\bFact\b", "saved data"),
    (r"\bDB worker\b", "database writer"),
    (r"\bDB queue\b", "write queue"),
    (r"(?<!write )\bqueue\b", "write queue"),
    (r"\bSQLite spool\b", "disk safety buffer"),
    (r"\bspool\b", "disk safety buffer"),
    (r"\boverflow buffer\b", "memory safety buffer"),
    (r"\bRAM overflow\b", "memory safety buffer"),
    (r"\bstaging rows\b", "temporary rows"),
    (r"\bstaging row\b", "temporary row"),
    (r"\bstaging\b", "temporary buffer"),
    (r"\bguest mode\b", "limited TradingView session"),
    (r"\bguest\b", "limited session"),
    (r"\bauth preflight\b", "login check"),
    (r"\bauth\b", "login"),
    (r"\bcached_token\b", "saved TradingView session"),
    (r"\bstatic_token\b", "configured TradingView session"),
    (r"\bsession_refresh\b", "browser session refresh"),
    (r"\bbrowser_profile\b", "saved browser profile"),
    (r"\bheadless_chromium\b", "automatic browser refresh"),
    (r"\bheadless_fresh_login\b", "automatic browser login"),
    (r"\btoken\b", "login token"),
    (r"\bcookie\b", "browser session"),
    (r"\bcooldown\b", "waiting period"),
    (r"\bstale\b", "outdated"),
    (r"\bsource lag\b", "feed delay"),
)

_REASON_KEYS = {"reason", "error", "last error", "failure reason"}
_IMPACT_KEYS = {"meaning", "impact", "why it matters", "effect"}
_ACTION_KEYS = {"suggested action", "recommended action", "next step", "next action"}

QUICK_COMMANDS_HINT = (
    "\n\n---\n"
    "Where to check:\n"
    "- Log files: `runtime/logs/`\n"
    "- Repair missing candles: run `python -m core_engine.core.historical.engine --mode gap`"
)


def _ensure_discord_logger() -> None:
    """Attach a dedicated Discord delivery log file once per process."""
    global _discord_logger_configured, _discord_logger_error
    if _discord_logger_configured:
        return
    with _discord_logger_lock:
        if _discord_logger_configured:
            return
        try:
            discord_log = process_scoped_log_path(DISCORD_LOG)
            discord_log.parent.mkdir(parents=True, exist_ok=True)
            handler = ResilientRotatingFileHandler(
                discord_log,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)-7s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S UTC",
                )
            )
            handler.formatter.converter = time.gmtime
            logger.addHandler(handler)
            register_log_sink(handler.baseFilename, logical_path=DISCORD_LOG)
            logger.setLevel(getattr(logging, LOGGING.level, logging.INFO))
            logger.propagate = False
            _discord_logger_error = None
            _discord_logger_configured = True
        except Exception as exc:
            # Keep propagation enabled so a failed dedicated sink remains
            # visible in the process/root crash capture.  A NullHandler here
            # used to turn logger setup failures into silent notification
            # failures for the rest of the process lifetime.
            logger.propagate = True
            _discord_logger_error = f"{type(exc).__name__}: {exc}"
            _discord_logger_configured = False
            logger.error(
                "Discord delivery logger setup failed: %s",
                exc.__class__.__name__,
            )


def _log_sender_exception(stage: str, exc: BaseException) -> None:
    """Best-effort diagnostics that never expose a webhook URL."""
    try:
        _ensure_discord_logger()
        logger.warning(
            "Discord sender %s failed: %s",
            stage,
            exc.__class__.__name__,
        )
    except Exception:
        # Logging must never become a second failure that kills the sender.
        try:
            print(
                f"Discord sender {stage} failed: {exc.__class__.__name__}",
                file=sys.stderr,
            )
        except Exception:
            pass


def sanitize_ssl_keylogfile(env: os._Environ[str] | dict[str, str] | None = None) -> str | None:
    """Remove unsafe TLS keylog targets from the process environment."""
    target = os.environ if env is None else env
    value = str(target.get("SSLKEYLOGFILE") or "").replace("/", "\\").strip()
    if not value.startswith(_UNSAFE_SSLKEYLOG_PREFIXES):
        return None
    try:
        target.pop("SSLKEYLOGFILE", None)
        target["DP_SSLKEYLOGFILE_IGNORED"] = value
    except Exception:
        return None
    return value


def _format_discord_text(message: str) -> str:
    """Convert legacy Telegram-style HTML into Discord Markdown."""
    text = unescape(str(message)).replace("\r\n", "\n").replace("\r", "\n")

    def _pre(match: re.Match[str]) -> str:
        body = match.group(1).strip("\n")
        return f"```\n{body}\n```"

    text = re.sub(r"<pre>(.*?)</pre>", _pre, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(b|strong)>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(i|em)>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _operator_text(text: str) -> str:
    """Make Discord text friendlier for an operator while keeping details useful."""
    display = str(text)
    for pattern, replacement in _OPERATOR_REPLACEMENTS:
        display = re.sub(pattern, replacement, display)
    display = re.sub(r"(?i)\[(green|yellow|red)\]\s*", "", display)
    display = re.sub(r"(?i)\b(green|yellow|red)\b\s*[:|-]?\s*", "", display)
    display = re.sub(r"\bDROPPED\b", "lost", display)
    display = re.sub(r"\bFAILED\b", "failed", display)
    display = re.sub(r"\bERROR\b", "error", display)
    display = re.sub(r"\bWARN(?:ING)?\b", "warning", display)
    return display.strip()


def _truncate_discord_text(text: str, limit: int) -> str:
    """Trim text to Discord limits while keeping code fences readable."""
    if len(text) <= limit:
        return text
    suffix = "\n...[truncated]"
    head = text[: max(0, limit - len(suffix))].rstrip()
    if head.count("```") % 2:
        suffix = "\n```\n...[truncated]"
        head = text[: max(0, limit - len(suffix))].rstrip()
    return head + suffix


def _truncate_plain_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + " ...[truncated]"


def _redact_sensitive_text(text: str) -> str:
    """Keep activity logs useful without storing webhook URLs or credentials."""
    safe = str(text)
    safe = re.sub(
        r"https://(?:discord(?:app)?\.com|discord\.com)/api/webhooks/\S+",
        "<discord_webhook>",
        safe,
        flags=re.IGNORECASE,
    )
    safe = re.sub(
        r"(?i)\b(auth[_-]?token|token|cookie|password|secret)\s*[:=]\s*[^,\s|;]+",
        lambda m: f"{m.group(1)}=<redacted>",
        safe,
    )
    safe = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", safe)
    return safe


def _activity_preview(text: str, limit: int = 900) -> str:
    safe = _redact_sensitive_text(text)
    safe = " | ".join(line.strip() for line in safe.splitlines() if line.strip())
    safe = re.sub(r"\s+", " ", safe).strip()
    return _truncate_plain_text(safe or "No details.", limit)


def _infer_feature(text: str) -> str:
    lower = text.lower()
    if any(
        token in lower
        for token in (
            "ws live",
            "ws batch",
            "live health",
            "live feed",
            "websocket",
            "live fetch",
            "closed candles",
        )
    ):
        return "live"
    if any(
        token in lower
        for token in (
            "[auth]",
            "tv-auth",
            "tv_auth",
            "tradingview auth",
            "tradingview session",
            "tradingview browser",
            "limited tradingview",
            "browser session refresh",
            "token",
            "cookie",
            "tradingview login",
        )
    ):
        return "tradingview_auth"
    if any(token in lower for token in ("backfill", "historical", "pipeline", "gap_batch", "hole")):
        return "historical"
    if "live" in lower:
        return "live"
    if any(token in lower for token in ("database", "sql", "db ", "db/", "staging", "fact", "warehouse")):
        return "database"
    if "config" in lower:
        return "config"
    if "log" in lower:
        return "logs"
    return "system"


def _infer_result(level: str, text: str) -> str:
    lower = text.lower()
    if "health report" in lower and any(token in lower for token in ("healthy", "everything looks normal")):
        return "healthy"
    if any(token in lower for token in ("critical", "failed", "[fail]", "[error]", " error ", "unreachable")):
        return "failed"
    if "cancelled" in lower or "canceled" in lower:
        return "cancelled"
    if any(token in lower for token in ("stopping", "shutdown requested")):
        return "stopping"
    if any(token in lower for token in ("stopped", "shutdown completed")):
        return "stopped"
    if "refreshing" in lower and "failed" not in lower:
        return "refreshing"
    if any(token in lower for token in ("started", "starting", "running", "ready")):
        return "started"
    if any(token in lower for token in ("finished", "completed", "success", "[ok]", " ok")):
        return "completed"
    if level in {"WARNING", "WARN"} or any(token in lower for token in ("warning", "warn", "stale", "timeout")):
        return "warning"
    if level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
        return "failed"
    if level in {"SUCCESS", "OK"}:
        return "completed"
    return "notified"


def _normalize_level(level: str | None) -> str:
    value = str(level or "INFO").strip().upper()
    aliases = {
        "GREEN": "SUCCESS",
        "YELLOW": "WARNING",
        "RED": "ERROR",
        "WARN": "WARNING",
    }
    value = aliases.get(value, value)
    return value if value in _COLORS else "INFO"


def _clean_title(text: str, level: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            break
    else:
        line = "SEN05 Data Provider"
    line = re.sub(r"[*_`>#]", "", line)
    line = re.sub(
        r"(?i)^\[(auth|live|ws|db|sched|startup|ok|info|warn|warning|error|fail|failed|crit|critical|start|stop)\]\s*",
        "",
        line,
    )
    line = re.sub(r"(?i)\[(green|yellow|red)\]\s*", "", line)
    line = re.sub(r"(?i)\b(green|yellow|red)\b\s*[:|-]?\s*", "", line)
    if "health report" in line.lower():
        line = re.sub(r"(?i)\s*:\s*(healthy|warning|critical)\s*$", "", line)
    line = re.sub(r"\s*[:|-]\s*$", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    prefix = _LEVEL_TITLES.get(level, "Info")
    if line.lower().startswith(prefix.lower()):
        title = line
    else:
        title = f"{prefix}: {line}"
    return title[:250]


def _split_labeled_line(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    key = key.strip().strip("*").lower()
    if not key or len(key) > 32:
        return None
    if re.search(r"https?$", key):
        return None
    return key, value.strip()


def _sentence(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    return value[0].upper() + value[1:]


def _body_headline(headline: str) -> str:
    value = str(headline or "").strip()
    if "health report" in value.lower():
        value = re.sub(r"(?i)\s*:\s*(healthy|warning|critical)\s*$", "", value)
    return value


def _default_impact(feature: str, result: str, level: str, headline: str) -> str:
    lower_headline = headline.lower()
    if "health report" in lower_headline:
        return "This is the scheduled health summary for the running data feed."
    if "waiting before reconnecting" in lower_headline or "waiting for tradingview" in lower_headline:
        return "The job is pausing briefly before retrying. Data can be delayed while it waits."
    if "no new closed candles" in lower_headline:
        return "The live connection answered, but no finished candle was available in that cycle."
    if "outdated data" in lower_headline:
        return "Some pairs are behind the current time. Backfill may be needed to close the gap."
    if feature == "live":
        if result == "failed" or level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
            return "Live OHLCV collection may pause or miss closed candles until this condition is cleared."
        if result == "warning" or level in {"WARNING", "WARN"}:
            return "Live OHLCV collection is still being monitored, but freshness or delivery can be affected."
        if result == "started":
            return "The live feed has started collecting closed candles on its configured schedule."
        if result == "stopped":
            return "The live feed is no longer collecting new candles."
        return "Live OHLCV collection is operating and reporting its latest state."
    if feature == "historical":
        if result == "failed":
            return "Historical repair/backfill did not complete, so missing OHLCV ranges may remain."
        if result in {"completed", "success"}:
            return "Historical OHLCV work completed and the warehouse should now be closer to the requested scope."
        return "Historical OHLCV work changed state and may affect gap repair or backfill coverage."
    if feature == "tradingview_auth":
        if result == "failed" or level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
            return "TradingView access may be limited, which can reduce history depth or block some symbols."
        return "TradingView login/session state affects both live fetching and historical pulling."
    if feature == "database":
        if result == "failed" or level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
            return "New candles may not be saved to the warehouse until the database write path recovers."
        return "Database write status controls whether fetched candles are persisted safely."
    if result == "failed" or level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
        return "The reported operation needs attention before the affected workflow can be trusted."
    if result == "warning" or level in {"WARNING", "WARN"}:
        return "The system is still running, but this condition should be watched."
    return "No immediate risk was detected from this notification."


def _default_action(feature: str, result: str, level: str) -> str:
    if result == "started":
        return "No action needed now. Watch the next health report or job summary."
    if result == "healthy":
        return "No action needed now. Keep the service running."
    if result == "refreshing":
        return "No action needed now. The system is refreshing access automatically."
    if result in {"completed", "success"}:
        return "No action needed unless the numbers look unexpected. If counts are low, open the matching log."
    if result in {"stopped", "cancelled"}:
        return "No action needed if this was intentional; otherwise restart the affected workflow."
    if feature == "live" and level in {"WARNING", "WARN"}:
        return "Monitor the next live cycle. If it repeats during market hours, check TradingView login, network, and SQL Server."
    if feature == "historical" and level in {"WARNING", "WARN"}:
        return "Review the historical log and run gap repair again if missing ranges remain."
    if feature == "tradingview_auth":
        return "Refresh TradingView login if the next run still reports limited access."
    if feature == "database":
        return "Check SQL Server connectivity, disk space, and database writer logs."
    if level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
        return "Open `runtime/logs/`, fix the reported reason, then restart or rerun the affected job."
    if level in {"WARNING", "WARN"}:
        return "Watch the next run. Investigate if the same warning repeats."
    return "No operator action needed."


def _operator_description(text: str, *, feature: str, result: str, level: str) -> str:
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines:
        return "No details."

    headline = _body_headline(raw_lines[0].strip())
    reason_lines: list[str] = []
    impact_lines: list[str] = []
    action_lines: list[str] = []
    detail_lines: list[str] = []

    for line in raw_lines[1:]:
        if line == "---":
            continue
        labeled = _split_labeled_line(line)
        if labeled:
            key, value = labeled
            if key in _REASON_KEYS:
                if value:
                    reason_lines.append(_sentence(value))
                continue
            if key in _IMPACT_KEYS:
                if value:
                    impact_lines.append(value)
                continue
            if key in _ACTION_KEYS:
                if value:
                    action_lines.append(value)
                continue
        detail_lines.append(line)

    impact = _sentence("\n".join(impact_lines) or _default_impact(feature, result, level, headline))
    action = _sentence("\n".join(action_lines) or _default_action(feature, result, level))

    status_line = _RESULT_LABELS.get(result, result.replace("_", " ").title())
    if result == "healthy":
        status_line = "Healthy"
    elif result == "refreshing":
        status_line = "Refreshing automatically"
    elif result == "started":
        status_line = "Started and waiting for the next scheduled report"
    elif result == "stopped":
        status_line = "Stopped"
    elif result == "cancelled":
        status_line = "Cancelled safely"
    elif level in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
        status_line = "Needs operator attention now"
    elif level in {"WARNING", "WARN"}:
        status_line = "Needs monitoring or a follow-up check"
    elif level in {"SUCCESS", "OK"}:
        status_line = "Completed successfully"

    sections = [
        ("What happened", headline),
        ("Current status", status_line),
        ("Why it matters", impact),
        ("Recommended action", action),
    ]
    if reason_lines:
        sections.append(("Reason", "\n".join(reason_lines)))
    if detail_lines:
        cleaned_details = [line for line in detail_lines[:18] if line.strip()]
        sections.append(("Details", "\n".join(cleaned_details)))

    return "\n\n".join(f"**{title}**\n{body}" for title, body in sections if body.strip())


def _build_embed_payload(*, level: str, text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = _normalize_level(level)
    raw_plain = _format_discord_text(text)
    if not raw_plain:
        raw_plain = "No details."
    plain = _operator_text(raw_plain)
    title = _clean_title(plain, normalized)
    feature = _infer_feature(raw_plain)
    result = _infer_result(normalized, raw_plain)
    description = _truncate_discord_text(
        _operator_description(plain, feature=feature, result=result, level=normalized),
        3400,
    )
    embed = {
        "title": title,
        "description": description,
        "color": _COLORS[normalized],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "SEN05 Data Provider"},
        "fields": [
            {"name": "Area", "value": _FEATURE_LABELS.get(feature, feature), "inline": True},
            {"name": "Severity", "value": _LEVEL_TITLES.get(normalized, normalized.title()), "inline": True},
            {
                "name": "Operator action",
                "value": _RESULT_ACTION_WORDS.get(result, result.replace("_", " ").title()),
                "inline": True,
            },
        ],
    }
    payload = {"embeds": [embed]}
    meta = {
        "notification_id": uuid4().hex[:16],
        "level": normalized,
        "feature": feature,
        "result": result,
        "title": title,
        "content_preview": _activity_preview(raw_plain),
        "display_preview": _activity_preview(plain),
        "content_hash": sha256(_redact_sensitive_text(raw_plain).encode("utf-8", errors="replace")).hexdigest()[:16],
        "content_lines": len(raw_plain.splitlines()),
        "payload_chars": len(title) + len(description),
        "embed_color": _COLORS[normalized],
        "embed_color_hex": f"#{_COLORS[normalized]:06X}",
    }
    return payload, meta


def _format_report_value(value: Any) -> str:
    """Render structured operator data without exposing Python object noise."""
    if value is None:
        return "-"
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if item is None or item == "":
                continue
            label = str(key).replace("_", " ").strip().title()
            lines.append(f"- {label}: {_redact_sensitive_text(str(item))}")
        return "\n".join(lines) if lines else "-"
    if isinstance(value, (list, tuple, set)):
        lines = [f"- {_redact_sensitive_text(str(item))}" for item in value if item is not None and str(item).strip()]
        return "\n".join(lines) if lines else "-"
    return _redact_sensitive_text(str(value).strip() or "-")


def _report_section(title: str, value: Any) -> str:
    return f"**{title}**\n{_format_report_value(value)}"


def _build_operator_report_payload(
    *,
    area: str,
    severity: str,
    title: str,
    summary: str,
    current_state: Any = None,
    data_result: Any = None,
    health_risk: Any = None,
    reason: Any = None,
    recommended_action: Any = None,
    trace: Any = None,
    result: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a Discord report card for non-technical operators.

    The legacy alert formatter is still kept for compatibility, but critical
    backend reports should prefer this builder because it separates what
    happened, current state, data impact, risk, root cause, action, and trace.
    """
    normalized = _normalize_level(severity)
    feature = str(area or "system").strip().lower() or "system"
    if feature == "tradingview":
        feature = "auth"
    label = _FEATURE_LABELS.get(feature, feature.replace("_", " ").title())
    raw_title = _redact_sensitive_text(str(title or label).strip() or label)
    display_title = _clean_title(raw_title, normalized)
    report_text = "\n".join(
        str(part)
        for part in (
            raw_title,
            summary,
            _format_report_value(current_state),
            _format_report_value(data_result),
            _format_report_value(health_risk),
            _format_report_value(reason),
            _format_report_value(recommended_action),
        )
        if str(part or "").strip()
    )
    report_result = str(result or _infer_result(normalized, report_text)).strip().lower()
    if result is None:
        if normalized in {"WARNING", "WARN"} and report_result not in {"cancelled", "stopping", "stopped"}:
            report_result = "warning"
        elif normalized in {"ERROR", "FAIL", "FAILED", "CRITICAL"}:
            report_result = "failed"
    if normalized in {"SUCCESS", "OK", "INFO"} and report_result in {"notified", "completed"}:
        operator_action = "No action needed"
    else:
        operator_action = _RESULT_ACTION_WORDS.get(report_result, _default_action(feature, report_result, normalized))
    description = _truncate_discord_text(
        "\n\n".join(
            [
                _report_section("Summary", summary or "No summary was provided."),
                _report_section("Current state", current_state or "No live state was provided."),
                _report_section("Data result", data_result or "No data change was reported."),
                _report_section("Health and risk", health_risk or _default_impact(feature, report_result, normalized, raw_title)),
                _report_section("Likely reason", reason or "No specific root cause was reported."),
                _report_section("Recommended action", recommended_action or _default_action(feature, report_result, normalized)),
                _report_section("Trace", trace or "No trace details."),
            ]
        ),
        3900,
    )
    embed = {
        "title": display_title,
        "description": description,
        "color": _COLORS[normalized],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "SEN05 Data Provider"},
        "fields": [
            {"name": "Area", "value": label, "inline": True},
            {"name": "Severity", "value": _LEVEL_TITLES.get(normalized, normalized.title()), "inline": True},
            {"name": "Operator action", "value": operator_action, "inline": True},
        ],
    }
    payload = {"embeds": [embed]}
    preview = _activity_preview(report_text)
    meta = {
        "notification_id": uuid4().hex[:16],
        "level": normalized,
        "feature": feature,
        "result": report_result,
        "title": display_title,
        "content_preview": preview,
        "display_preview": preview,
        "content_hash": sha256(_redact_sensitive_text(report_text).encode("utf-8", errors="replace")).hexdigest()[:16],
        "content_lines": len(report_text.splitlines()),
        "payload_chars": len(display_title) + len(description),
        "embed_color": _COLORS[normalized],
        "embed_color_hex": f"#{_COLORS[normalized]:06X}",
    }
    return payload, meta


def _record_discord_activity(
    action: str,
    *,
    status: str,
    kind: str,
    level: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    _ensure_discord_logger()
    safe_detail = {"kind": kind, "level": level}
    if detail:
        safe_detail.update(detail)
    if status == "failed":
        log_level = logging.WARNING
    elif status in {"warning", "queued"}:
        log_level = logging.INFO if action in {"discord.skipped", "discord.suppressed"} else logging.DEBUG
    elif action == "discord.alert":
        log_level = logging.INFO
    elif action == "discord.sent":
        log_level = logging.INFO
    else:
        log_level = logging.DEBUG
    details = []
    for key in (
        "delivery_status",
        "delivery_result",
        "http_status",
        "attempts",
        "duration_seconds",
        "business_status",
        "business_result",
        "embed_color_hex",
        "content_hash",
        "suppressed_count",
    ):
        if key in safe_detail:
            details.append(f"{key}={safe_detail.get(key)}")
    suffix = (" | " + " | ".join(details)) if details else ""
    delivery_status = (
        safe_detail.get("delivery_status")
        or safe_detail.get("delivery_result")
        or ("failed" if action == "discord.failed" else status)
    )
    event_status = safe_detail.get("business_status") or status
    logger.log(
        log_level,
        "%s",
        operation_line(
            "DISCORD",
            "Delivery event",
            action=action,
            delivery=delivery_status,
            event_area=safe_detail.get("feature", "system"),
            event_status=event_status,
            event_result=safe_detail.get("result", "notified"),
            title=safe_detail.get("title", message),
            detail=suffix.strip(" |") if suffix else "",
        ),
    )


def _safe_record_discord_activity(action: str, **kwargs: Any) -> None:
    """Record delivery telemetry without changing the delivery outcome."""
    try:
        _record_discord_activity(action, **kwargs)
    except Exception as exc:
        _log_sender_exception("activity_log", exc)


def _discord_circuit_remaining() -> float:
    with _discord_circuit_lock:
        return max(0.0, _discord_circuit_open_until - time.time())


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_discord_status(event: str) -> None:
    """Publish non-secret sender health for doctor from another process."""
    try:
        role = process_role()
        if role not in {"supervisor", "live", "historical"}:
            return
        with _discord_status_lock:
            payload = {
                "role": role,
                "pid": os.getpid(),
                "updated_at": _utc_iso_now(),
                "event": event,
                "configured": bool(NOTIFICATION.discord_webhook_url),
                "last_success_at": _discord_last_success_at,
                "last_failure_at": _discord_last_failure_at,
                "last_failure_error": _discord_last_failure_error,
                "consecutive_failures": _discord_failure_count,
                "circuit_open_seconds": round(_discord_circuit_remaining(), 3),
                "logger_error": _discord_logger_error,
                "queue_rejections": _discord_queue_rejections,
                **_discord_sender.status(),
            }
            target = RUN_DIR / "notification_status" / f"{role}.{os.getpid()}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temp, target)
    except Exception as exc:
        _log_sender_exception("status_write", exc)


def _discord_mark_success() -> None:
    global _discord_circuit_open_until, _discord_failure_count
    global _discord_last_success_at, _discord_last_failure_error
    with _discord_circuit_lock:
        _discord_failure_count = 0
        _discord_circuit_open_until = 0.0
        _discord_last_success_at = _utc_iso_now()
        _discord_last_failure_error = None
    _persist_discord_status("delivery_success")


def _discord_mark_failure(error: str | None = None) -> tuple[int, float]:
    global _discord_circuit_open_until, _discord_failure_count
    global _discord_last_failure_at, _discord_last_failure_error
    with _discord_circuit_lock:
        _discord_failure_count += 1
        _discord_last_failure_at = _utc_iso_now()
        _discord_last_failure_error = error or "delivery_failed"
        if _discord_failure_count >= DISCORD_CIRCUIT_FAILURES:
            _discord_circuit_open_until = time.time() + DISCORD_CIRCUIT_COOLDOWN_SEC
        result = _discord_failure_count, max(0.0, _discord_circuit_open_until - time.time())
    _persist_discord_status("delivery_failure")
    return result


def _dedupe_key(kind: str, level: str, meta: dict[str, Any]) -> str:
    return ":".join(
        [
            kind,
            level,
            str(meta.get("feature", "system")),
            str(meta.get("result", "notified")),
            str(meta.get("content_hash", "")),
        ]
    )


def _duplicate_suppression(
    *,
    kind: str,
    level: str,
    meta: dict[str, Any],
) -> tuple[bool, int, float]:
    if DISCORD_DEDUPE_WINDOW_SEC <= 0:
        return False, 0, 0.0
    if level not in {"WARNING", "ERROR", "FAIL", "FAILED", "CRITICAL"}:
        return False, 0, 0.0

    global _discord_last_dedupe_cleanup
    now = time.time()
    key = _dedupe_key(kind, level, meta)
    with _discord_dedupe_lock:
        if (
            now - _discord_last_dedupe_cleanup >= 60
            or len(_discord_last_sent) > DISCORD_DEDUPE_MAX_KEYS
        ):
            cutoff = now - max(1, DISCORD_DEDUPE_WINDOW_SEC)
            stale = [item for item, sent_at in _discord_last_sent.items() if sent_at < cutoff]
            for item in stale:
                _discord_last_sent.pop(item, None)
                _discord_suppressed.pop(item, None)
            if len(_discord_last_sent) > DISCORD_DEDUPE_MAX_KEYS:
                overflow = len(_discord_last_sent) - DISCORD_DEDUPE_MAX_KEYS
                for item, _sent_at in sorted(
                    _discord_last_sent.items(), key=lambda pair: pair[1]
                )[:overflow]:
                    _discord_last_sent.pop(item, None)
                    _discord_suppressed.pop(item, None)
            for item in list(_discord_suppressed):
                if item not in _discord_last_sent:
                    _discord_suppressed.pop(item, None)
            _discord_last_dedupe_cleanup = now
        last = _discord_last_sent.get(key, 0.0)
        elapsed = now - last
        if last and elapsed < DISCORD_DEDUPE_WINDOW_SEC:
            _discord_suppressed[key] = _discord_suppressed.get(key, 0) + 1
            return True, _discord_suppressed[key], DISCORD_DEDUPE_WINDOW_SEC - elapsed

        # Do not reserve the dedupe window here.  The worker only records a
        # last-sent timestamp after Discord confirms HTTP 200/204.  This is
        # what allows a duplicate queued behind a failed send to retry.
        suppressed = _discord_suppressed.get(key, 0)
        return False, suppressed, 0.0


def _mark_duplicate_delivered(*, kind: str, level: str, meta: dict[str, Any]) -> None:
    if DISCORD_DEDUPE_WINDOW_SEC <= 0:
        return
    if level not in {"WARNING", "ERROR", "FAIL", "FAILED", "CRITICAL"}:
        return
    key = _dedupe_key(kind, level, meta)
    with _discord_dedupe_lock:
        suppressed = _discord_suppressed.pop(key, 0)
        _discord_last_sent[key] = time.time()
    if suppressed:
        meta["duplicate_suppressed_since_last"] = suppressed


def _post_payload(
    payload: dict[str, Any],
    *,
    kind: str,
    level: str,
    meta: dict[str, Any],
) -> bool:
    """Post one payload and return True only on Discord HTTP 200/204."""
    started_at = time.time()
    try:
        _ensure_discord_logger()
        sanitize_ssl_keylogfile()
    except Exception as exc:
        _log_sender_exception("setup", exc)
        fail_count, circuit_remaining = _discord_mark_failure(exc.__class__.__name__)
        _safe_record_discord_activity(
            "discord.failed",
            status="failed",
            kind=kind,
            level=level,
            message=(
                f"Discord {kind} setup failed: "
                f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
                f"{meta.get('title', '')}"
            ),
            detail={
                **meta,
                "delivery_result": "failed",
                "error": exc.__class__.__name__,
                "attempts": 0,
                "duration_seconds": round(time.time() - started_at, 3),
                "consecutive_failures": fail_count,
                "circuit_open_seconds": round(circuit_remaining, 1),
            },
        )
        return False

    circuit_remaining = _discord_circuit_remaining()
    if circuit_remaining > 0:
        logger.warning(
            "%s",
            operation_line(
                "DISCORD",
                "Send skipped",
                notification_type=kind,
                event_level=level,
                reason="circuit_open",
                retry_after_seconds=round(circuit_remaining),
                result="skipped",
            ),
        )
        _safe_record_discord_activity(
            "discord.skipped",
            status="warning",
            kind=kind,
            level=level,
            message=(
                f"Discord {kind} skipped: webhook circuit open - "
                f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')}"
            ),
            detail={
                **meta,
                "delivery_result": "skipped",
                "reason": "circuit_open",
                "retry_after_seconds": round(circuit_remaining, 1),
            },
        )
        return False

    last_status: int | None = None
    last_error: str | None = None
    attempts = 0
    for attempt in range(DISCORD_SEND_ATTEMPTS):
        attempts = attempt + 1
        result = post_webhook_once(
            NOTIFICATION.discord_webhook_url,
            payload,
            connect_timeout=DISCORD_TIMEOUT_CONNECT_SEC,
            read_timeout=DISCORD_TIMEOUT_READ_SEC,
        )
        if result.error is not None:
            last_error = result.error.__class__.__name__
            _log_sender_exception(
                "setup" if result.stage == "setup" else f"http_attempt_{attempts}",
                result.error,
            )
            if result.stage == "setup":
                fail_count, circuit_remaining = _discord_mark_failure(last_error)
                _safe_record_discord_activity(
                    "discord.failed",
                    status="failed",
                    kind=kind,
                    level=level,
                    message=(
                        f"Discord {kind} setup failed: "
                        f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
                        f"{meta.get('title', '')}"
                    ),
                    detail={
                        **meta,
                        "delivery_result": "failed",
                        "error": last_error,
                        "attempts": 0,
                        "duration_seconds": round(time.time() - started_at, 3),
                        "consecutive_failures": fail_count,
                        "circuit_open_seconds": round(circuit_remaining, 1),
                    },
                )
                return False
            if attempt + 1 < DISCORD_SEND_ATTEMPTS:
                time.sleep(1.5)
            continue

        last_status = result.status_code
        if result.ok:
                logger.debug(
                    "%s",
                    operation_line(
                        "DISCORD",
                        "Send completed",
                        notification_type=kind,
                        event_level=level,
                        http_status=result.status_code,
                        result="delivered",
                    ),
                )
                _discord_mark_success()
                business_status = _STATUS_BY_LEVEL.get(level, "success")
                _safe_record_discord_activity(
                    "discord.sent",
                    status="success",
                    kind=kind,
                    level=level,
                    message=(
                        f"Discord {kind} delivered: "
                        f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
                        f"{meta.get('title', '')}"
                    ),
                    detail={
                        **meta,
                        "delivery_result": "sent",
                        "delivery_status": "success",
                        "business_level": level,
                        "business_status": business_status,
                        "business_result": meta.get("result", "notified"),
                        "http_status": result.status_code,
                        "attempts": attempts,
                        "duration_seconds": round(time.time() - started_at, 3),
                    },
                )
                if business_status in {"warning", "failed"}:
                    _safe_record_discord_activity(
                        "discord.alert",
                        status=business_status,
                        kind=kind,
                        level=level,
                        message=(
                            f"Discord {kind} business alert: "
                            f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
                            f"{meta.get('title', '')}"
                        ),
                        detail={
                            **meta,
                            "delivery_result": "sent",
                            "delivery_status": "success",
                            "business_level": level,
                            "business_status": business_status,
                            "business_result": meta.get("result", "notified"),
                            "http_status": result.status_code,
                            "attempts": attempts,
                            "duration_seconds": round(time.time() - started_at, 3),
                        },
                    )
                return True
        if result.status_code == 429:
            time.sleep(min(result.retry_after_seconds or 5.0, 5.0))
        elif attempt + 1 < DISCORD_SEND_ATTEMPTS:
            time.sleep(1.5)
    fail_count, circuit_remaining = _discord_mark_failure(last_error or str(last_status or "http_failure"))
    logger.warning(
        "%s",
        operation_line(
            "DISCORD",
            "Send failed after retries",
            notification_type=kind,
            event_level=level,
            http_status=last_status or "none",
            reason=last_error or "none",
            consecutive_failures=fail_count,
            circuit_open_seconds=round(circuit_remaining),
            result="failed",
        ),
    )
    _safe_record_discord_activity(
        "discord.failed",
        status="failed",
        kind=kind,
        level=level,
        message=(
            f"Discord {kind} failed: "
            f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
            f"{meta.get('title', '')}"
        ),
        detail={
            **meta,
            "delivery_result": "failed",
            "http_status": last_status,
            "error": last_error or "none",
            "attempts": attempts,
            "duration_seconds": round(time.time() - started_at, 3),
            "consecutive_failures": fail_count,
            "circuit_open_seconds": round(circuit_remaining, 1),
        },
    )
    return False


def _deliver_item(item: _DiscordSendItem) -> None:
    """Apply delivery-time dedupe and send one item on the sole worker."""
    suppressed, suppressed_count, retry_after = _duplicate_suppression(
        kind=item.kind,
        level=item.level,
        meta=item.meta,
    )
    if suppressed:
        _safe_record_discord_activity(
            "discord.suppressed",
            status="warning",
            kind=item.kind,
            level=item.level,
            message=(
                f"Discord {item.kind} duplicate suppressed: "
                f"{item.meta.get('feature', 'system')}/"
                f"{item.meta.get('result', 'notified')} - "
                f"{item.meta.get('title', '')}"
            ),
            detail={
                **item.meta,
                "delivery_result": "suppressed",
                "reason": "duplicate_within_window",
                "suppressed_count": suppressed_count,
                "retry_after_seconds": round(retry_after, 1),
            },
        )
        return

    if suppressed_count:
        item.meta["duplicate_suppressed_since_last"] = suppressed_count
    delivered = _post_payload(
        item.payload,
        kind=item.kind,
        level=item.level,
        meta=item.meta,
    )
    if delivered:
        _mark_duplicate_delivered(kind=item.kind, level=item.level, meta=item.meta)


def _start_sender(payload: dict[str, Any], *, kind: str, level: str, meta: dict[str, Any]) -> None:
    global _discord_queue_rejections
    item = _DiscordSendItem(payload=payload, kind=kind, level=level, meta=meta)
    reject_reason = "queue_full"
    try:
        accepted = _discord_sender.submit(item)
    except Exception as exc:
        _log_sender_exception("queue_submit", exc)
        accepted = False
        reject_reason = "queue_unavailable"
    if not accepted:
        with _discord_status_lock:
            _discord_queue_rejections += 1
        _persist_discord_status("queue_rejected")
        _safe_record_discord_activity(
            "discord.queue_rejected",
            status="failed",
            kind=kind,
            level=level,
            message=(
                f"Discord {kind} rejected by bounded sender queue ({reject_reason}) - "
                f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
                f"{meta.get('title', '')}"
            ),
            detail={
                **meta,
                "delivery_result": "failed",
                "reason": reject_reason,
            },
        )
        return

    _safe_record_discord_activity(
        "discord.queued",
        status="queued",
        kind=kind,
        level=level,
        message=(
            f"Discord {kind} queued: "
            f"{meta.get('feature', 'system')}/{meta.get('result', 'notified')} - "
            f"{meta.get('title', '')}"
        ),
        detail={**meta, "delivery_result": "queued"},
    )
    _persist_discord_status("queued")


def flush_pending(timeout: float = 12.0) -> bool:
    """Wait up to one total timeout for the bounded sender queue to drain."""
    return _discord_sender.flush(timeout)


def send_alert(level: str, text: str) -> None:
    """Send a level-specific Discord alert as an embed with a color side line."""
    if not NOTIFICATION.discord_webhook_url:
        return
    normalized = _normalize_level(level)
    payload, meta = _build_embed_payload(level=normalized, text=text)
    _start_sender(payload, kind="alert", level=normalized, meta=meta)


def notify_operator_report(
    *,
    area: str,
    severity: str,
    title: str,
    summary: str,
    current_state: Any = None,
    data_result: Any = None,
    health_risk: Any = None,
    reason: Any = None,
    recommended_action: Any = None,
    trace: Any = None,
    result: str | None = None,
) -> None:
    """Send a structured Discord report written for operators, not code readers."""
    if not NOTIFICATION.discord_webhook_url:
        return
    payload, meta = _build_operator_report_payload(
        area=area,
        severity=severity,
        title=title,
        summary=summary,
        current_state=current_state,
        data_result=data_result,
        health_risk=health_risk,
        reason=reason,
        recommended_action=recommended_action,
        trace=trace,
        result=result,
    )
    _start_sender(payload, kind="operator_report", level=meta["level"], meta=meta)


def notify_backend_event(*, severity: str, title: str, summary: str, **kwargs: Any) -> None:
    notify_operator_report(area="backend", severity=severity, title=title, summary=summary, **kwargs)


def notify_live_event(*, severity: str, title: str, summary: str, **kwargs: Any) -> None:
    notify_operator_report(area="live", severity=severity, title=title, summary=summary, **kwargs)


def notify_historical_event(*, severity: str, title: str, summary: str, **kwargs: Any) -> None:
    notify_operator_report(area="historical", severity=severity, title=title, summary=summary, **kwargs)


def notify_auth_event(*, severity: str, title: str, summary: str, **kwargs: Any) -> None:
    notify_operator_report(area="auth", severity=severity, title=title, summary=summary, **kwargs)

