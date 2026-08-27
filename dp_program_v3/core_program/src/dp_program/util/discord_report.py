"""Non-blocking Discord reporting tied to the engine service lifecycle."""
# Reporter Discord chạy cùng service live/backfill.
# Gửi Discord lỗi cũng không được làm dừng engine chính.

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from ..log import log_event

LOGGER = logging.getLogger(__name__)
# Health gửi định kỳ; cảnh báo lặp bị throttle để tránh spam.
_PERIOD_SECONDS = 3 * 3600
_DUPLICATE_SECONDS = 900
_SPOOL_PENDING_WARNING_SECONDS = 120
# Màu embed theo mức rủi ro.
_COLORS = {"NONE": 0x2ECC71, "MEDIUM": 0xF1C40F, "HIGH": 0xE67E22, "CRITICAL": 0xE74C3C}
# Gợi ý hành động ngắn cho operator trong Discord.
_ACTIONS = {
    "NONE": "No operator action required",
    "MEDIUM": "Monitor the next cycle and inspect logs if it persists",
    "HIGH": "Inspect the affected component and recent HIGH-risk logs",
    "CRITICAL": "Operator review is required; data delivery may be paused",
}
_Post = Callable[..., Any]


def _redact(value: object) -> str:
    """Return bounded operator text with common credentials removed."""
    # Che webhook/token/password trước khi đưa text lên Discord.
    text = str(value)
    text = re.sub(
        r"https://(?:discord(?:app)?\.com)/api/webhooks/[^\s\"']+",
        "[REDACTED_WEBHOOK]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?i)\b(password|cookie|auth_token|token|secret|webhook_url)\b"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED_JWT]", text)
    return text[:1000]


def _risk(snapshot: dict[str, Any]) -> str:
    # Tính mức rủi ro từ snapshot runtime hiện tại.
    explicit = str(snapshot.get("risk") or "").upper()
    if explicit in _COLORS:
        return explicit
    if snapshot.get("error_type") or snapshot.get("error"):
        return "CRITICAL"
    auth = snapshot.get("auth") or {}
    database = snapshot.get("database") or {}
    spool = snapshot.get("spool") or {}
    live = snapshot.get("last_live") or {}
    backfill = snapshot.get("last_backfill") or {}
    if auth.get("ok") is False or database.get("ok") is False:
        return "CRITICAL"
    if (
        int(spool.get("corrupt") or 0)
        or int(live.get("failed") or 0)
        or int(backfill.get("failed") or 0)
        or bool(snapshot.get("backfill_failed_pairs"))
    ):
        return "HIGH"
    spool_pending = int(spool.get("pending") or 0)
    spool_age = float(spool.get("oldest_age_seconds") or 0)
    if spool_age > 900:
        return "HIGH"
    progress = snapshot.get("last_backfill_progress_at")
    if int(snapshot.get("backfill_queue_remaining") or 0) and progress:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(progress))
            if age.total_seconds() > 3600:
                return "HIGH"
        except (TypeError, ValueError):
            pass
    # Spool pending vài giây là trạng thái bình thường khi đang commit SQL.
    # Chỉ nâng cảnh báo nếu file tạm tồn tại đủ lâu để giống dấu hiệu kẹt.
    if (spool_pending and spool_age > _SPOOL_PENDING_WARNING_SECONDS) or int(live.get("deferred") or 0):
        return "MEDIUM"
    return "NONE"


def _field(name: str, value: object, *, inline: bool = True) -> dict[str, Any]:
    # Một field nhỏ trong Discord embed, luôn đi qua redact.
    return {"name": name, "value": _redact(value) or "-", "inline": inline}


def _build_payload(snapshot: dict[str, Any], event: str) -> dict[str, Any]:
    """Build one concise embed from a secret-free runtime snapshot."""
    # Tạo payload ngắn: process, heartbeat, auth, SQL, live, backfill, spool.
    risk = _risk(snapshot)
    status = "HEALTHY" if risk == "NONE" else ("WARNING" if risk == "MEDIUM" else risk)
    service_status = str(snapshot.get("status") or "unknown").title()
    pid = snapshot.get("pid") or "-"
    auth = snapshot.get("auth") or {}
    database = snapshot.get("database") or {}
    live = snapshot.get("last_live") or {}
    spool = snapshot.get("spool") or {}
    auth_text = str(auth.get("state") or ("ready" if auth.get("ok") else "unknown"))
    if auth.get("source"):
        auth_text += f" · {auth['source']}"
    if auth.get("seconds_remaining") is not None:
        auth_text += f" · token {int(auth['seconds_remaining']) // 60}m remaining"
    live_text = "No completed cycle"
    if live:
        live_text = (
            f"{int(live.get('ok') or 0)}/{int(live.get('pairs') or 0)} successful"
            f" · {int(live.get('failed') or 0)} failed"
            f" · {int(live.get('affected') or 0)} affected"
        )
    captured = snapshot.get("captured_at") or datetime.now(timezone.utc).isoformat()
    fields = [
        _field("Process", f"PID {pid} · {service_status}"),
        _field("Heartbeat", snapshot.get("heartbeat_at") or captured),
        _field("TradingView", auth_text),
        _field(
            "SQL Server",
            f"{'Connected' if database.get('ok') else 'Unavailable'}"
            f" · {database.get('database') or 'unknown'}",
        ),
        _field("Live", live_text, inline=False),
        _field(
            "Backfill",
            f"{int(snapshot.get('backfill_queue_remaining') or 0)} queued; "
            f"{int(snapshot.get('bootstrap_remaining_pairs') or 0)} need policy bootstrap",
        ),
        _field(
            "Spool",
            f"{int(spool.get('pending') or 0)} pending"
            f" · {int(spool.get('corrupt') or 0)} corrupt",
        ),
        _field("Risk", risk),
        _field("Action", _ACTIONS[risk], inline=False),
    ]
    if snapshot.get("last_backfill_generation"):
        fields.insert(6, _field("Backfill Generation", snapshot["last_backfill_generation"], inline=False))
    if snapshot.get("error_type"):
        fields.insert(-2, _field("Error", snapshot["error_type"], inline=False))
    return {
        "username": "DP Program",
        "embeds": [{
            "title": f"DP Program — {status}",
            "description": f"Event: `{_redact(event).upper()}`",
            "color": _COLORS[risk],
            "fields": fields,
            "footer": {"text": f"UTC · {_redact(captured)}"},
        }],
    }


def _post_payload(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    post: _Post = requests.post,
    sleep: Callable[[float], Any] = time.sleep,
    attempts: int = 3,
) -> int:
    """Send with bounded retry; only Discord 200/204 responses are success."""
    # Gửi webhook với retry có giới hạn; HTTP 429 thì tôn trọng retry_after.
    last_status = 0
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = post(webhook_url, json=payload, timeout=5.0)
            last_status = int(response.status_code)
            if last_status in (200, 204):
                return last_status
            delay = 1.0
            if last_status == 429:
                try:
                    delay = min(5.0, max(0.1, float(response.json().get("retry_after", 1))))
                except (TypeError, ValueError):
                    delay = 1.0
        except Exception as exc:  # requests and injected transports
            last_error = exc
            delay = 1.0
        if attempt < attempts:
            sleep(delay)
    detail = f"HTTP {last_status}" if last_status else type(last_error).__name__
    raise RuntimeError(f"Discord delivery failed: {detail}") from last_error


def send_watchdog_alert(
    config: dict[str, Any], event: str, snapshot: dict[str, Any], *, post: _Post = requests.post
) -> int:
    """Send one immediate alert from a short-lived script outside the engine.

    DiscordReporter needs a background worker thread bound to the running
    engine's lifecycle; a periodic watchdog process has neither, so it
    calls straight through to the same payload/post building blocks.
    """
    # scripts/windows/watchdog.py gọi hàm này; không qua DiscordReporter
    # vì watchdog chạy ngắn hạn, không có thread nền để publish() vào.
    settings = config.get("discord") or {}
    webhook_url = str(settings.get("webhook_url") or "")
    if not bool(settings.get("enabled")) or not webhook_url:
        return 0
    return _post_payload(webhook_url, _build_payload(snapshot, event), post=post)


class DiscordReporter:
    """Own one bounded worker while, and only while, the engine is running."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        post: _Post = requests.post,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Chỉ chuẩn bị worker; thread thật được mở trong __enter__.
        settings = config.get("discord") or {}
        self.enabled = bool(settings.get("enabled"))
        self.webhook_url = str(settings.get("webhook_url") or "")
        if self.enabled and not self.webhook_url.startswith(
            ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
        ):
            raise ValueError("discord.webhook_url must be a Discord HTTPS webhook")
        self.post = post
        self.clock = clock
        self.items: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=16)
        self.thread: threading.Thread | None = None
        self.last_sent = float("-inf")
        self.last_risk = "NONE"
        self.last_signature: tuple[Any, ...] | None = None

    def __enter__(self) -> "DiscordReporter":
        # Worker chỉ sống khi service đang chạy trong context này.
        if self.enabled:
            self.thread = threading.Thread(
                target=self._worker, name="dp-discord-reporter", daemon=True
            )
            self.thread.start()
        return self

    def publish(self, event: str, state: dict[str, Any]) -> None:
        # Quyết định gửi hay bỏ qua để Discord không bị spam.
        if not self.enabled or self.thread is None or not self.thread.is_alive():
            return
        snapshot = deepcopy(state)
        snapshot["captured_at"] = datetime.now(timezone.utc).isoformat()
        risk = _risk(snapshot)
        now = self.clock()
        recovered = self.last_risk != "NONE" and risk == "NONE"
        signature = (
            risk,
            snapshot.get("error_type"),
            int((snapshot.get("last_live") or {}).get("failed") or 0),
            int((snapshot.get("last_backfill") or {}).get("failed") or 0),
            bool((snapshot.get("auth") or {}).get("ok", True)),
            bool((snapshot.get("database") or {}).get("ok", True)),
            int((snapshot.get("spool") or {}).get("corrupt") or 0),
        )
        lifecycle = event in {"started", "stopped", "failed", "backfill_completed"}
        changed_problem = risk != "NONE" and signature != self.last_signature
        repeated_problem = risk != "NONE" and now - self.last_sent >= _DUPLICATE_SECONDS
        periodic_due = risk == "NONE" and now - self.last_sent >= _PERIOD_SECONDS
        if not (lifecycle or recovered or changed_problem or repeated_problem or periodic_due):
            self.last_risk, self.last_signature = risk, signature
            return
        selected_event = "recovered" if recovered else event
        try:
            self.items.put_nowait((selected_event, snapshot))
            self.last_sent = now
        except queue.Full:
            log_event(
                LOGGER, logging.WARNING, "DISCORD_QUEUE_FULL", "MEDIUM",
                component="discord", action="report dropped; engine continues",
            )
        self.last_risk, self.last_signature = risk, signature

    def _worker(self) -> None:
        # Worker nền gửi HTTP; lỗi Discord chỉ ghi log, engine vẫn chạy tiếp.
        while True:
            item = self.items.get()
            if item is None:
                return
            event, snapshot = item
            try:
                status = _post_payload(self.webhook_url, _build_payload(snapshot, event), post=self.post)
                log_event(
                    LOGGER, logging.INFO, "DISCORD_REPORT_SENT", "NONE",
                    component="discord", report_event=event, http_status=status,
                )
            except Exception as exc:
                log_event(
                    LOGGER, logging.ERROR, "DISCORD_REPORT_FAILED", "HIGH",
                    component="discord", report_event=event,
                    error_type=type(exc).__name__, action="engine continues; inspect connectivity",
                )

    def close(self) -> None:
        # Dừng worker có timeout để shutdown service không bị treo.
        if self.thread is None:
            return
        try:
            self.items.put_nowait(None)
        except queue.Full:
            log_event(
                LOGGER, logging.WARNING, "DISCORD_SHUTDOWN_QUEUE_FULL", "MEDIUM",
                component="discord", action="bounded shutdown continues",
            )
        self.thread.join(timeout=12)
        if self.thread.is_alive():
            log_event(
                LOGGER, logging.WARNING, "DISCORD_SHUTDOWN_TIMEOUT", "MEDIUM",
                component="discord", action="daemon reporter left for process exit",
            )

    def __exit__(self, exc_type: Any, _exc: Any, _traceback: Any) -> bool:
        # Nếu service thoát vì exception, cố gửi một report "failed" cuối.
        if exc_type is not None:
            self.publish("failed", {"status": "failed", "error_type": exc_type.__name__})
        self.close()
        return False
