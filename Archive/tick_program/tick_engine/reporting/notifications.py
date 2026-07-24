"""Discord webhook notifications for tick_engine.

Merged from:
  tick_program/engine/common/notifications.py  — low-level webhook layer
  tick_program/engine/tick_data/notify.py      — tick-specific report helpers

Public API:
  tg_send(message)                — plain-text message
  tg_alert(level, text)           — colored embed (INFO / WARNING / ERROR / CRITICAL)
  tg_flush(timeout)               — wait for pending sends
  notify_tick(level, title, ...)  — throttled operator-facing embed
  notify_tick_report(level, ...)  — full structured report embed
  flush_notifications(timeout)    — safe wrapper around tg_flush
  build_tick_report_text(...)     — build report body string
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from html import unescape
from typing import Any

logger = logging.getLogger(__name__)

_pending_threads: list[threading.Thread] = []
_last_sent: dict[str, float] = {}

_COLORS = {
    "INFO": 3447003,
    "WARNING": 16776960,
    "ERROR": 15158332,
    "CRITICAL": 10038562,
}
_LEVEL_LABEL = {
    "INFO": "Info",
    "WARNING": "Warning",
    "ERROR": "Error",
    "CRITICAL": "Critical",
}


def _system_event(item: str, detail: str = "", *, level: str = "INFO") -> None:
    try:
        from tick_engine.reporting.system_log import write_system_event

        write_system_event("Discord Notify", item, detail, level=level)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_html(message: str) -> str:
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


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n...[message truncated]"
    head = text[: max(0, limit - len(suffix))].rstrip()
    if head.count("```") % 2:
        suffix = "\n```\n...[message truncated]"
        head = text[: max(0, limit - len(suffix))].rstrip()
    return head + suffix


def _spawn(fn: Any) -> None:
    global _pending_threads
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    _pending_threads = [x for x in _pending_threads if x.is_alive()]
    _pending_threads.append(t)


def _post_webhook(payload: dict) -> None:
    import requests

    from tick_engine.settings import DISCORD_WEBHOOK_URL

    if not DISCORD_WEBHOOK_URL:
        return
    for attempt in range(3):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10, verify=True)
            if resp.status_code in (200, 204):
                _system_event("sent", f"status={resp.status_code} | attempt={attempt + 1}")
                return
            if resp.status_code == 429:
                wait = 5.0
                try:
                    wait = float(resp.json().get("retry_after", 5))
                except Exception:
                    pass
                _system_event(
                    "rate limited",
                    f"attempt={attempt + 1} | sleep={min(wait, 30):g}s",
                    level="WARNING",
                )
                time.sleep(min(wait, 30))
            elif attempt < 2:
                _system_event(
                    "http failed",
                    f"status={resp.status_code} | attempt={attempt + 1}",
                    level="WARNING",
                )
                time.sleep(3)
            else:
                _system_event(
                    "http failed",
                    f"status={resp.status_code} | attempt={attempt + 1}",
                    level="ERROR",
                )
        except Exception:
            _system_event("send failed", f"attempt={attempt + 1}", level="WARNING")
            if attempt < 2:
                time.sleep(3)
    _system_event("give up", "webhook send failed after 3 attempts", level="ERROR")


def _stringify(value: Any) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _as_rows(items: Iterable[object] | None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in items or ():
        if isinstance(item, tuple) and len(item) == 2:
            rows.append((_stringify(item[0]), _stringify(item[1])))
            continue
        text = _stringify(item)
        if "=" in text and "\n" not in text:
            key, value = text.split("=", 1)
            rows.append((key.strip(), value.strip()))
        else:
            rows.append(("", text))
    return rows


def _detail_block(items: Iterable[object] | None) -> str:
    rows = _as_rows(items)
    if not rows:
        return ""
    label_width = min(18, max((len(label) for label, _value in rows if label), default=0))
    lines: list[str] = []
    for label, value in rows:
        if label:
            lines.append(f"{label.ljust(label_width)} : {value}")
        else:
            lines.append(value)
    return "```text\n" + "\n".join(lines) + "\n```"


# ---------------------------------------------------------------------------
# Low-level webhook API
# ---------------------------------------------------------------------------


def tg_send(message: str) -> None:
    """Send a plain-text message via Discord webhook (background thread)."""
    from tick_engine.settings import DISCORD_WEBHOOK_URL

    if not DISCORD_WEBHOOK_URL:
        return
    plain = _trim(_strip_html(message), 2000)

    def _send() -> None:
        _post_webhook({"content": plain[:2000]})

    _spawn(_send)


def tg_alert(level: str, text: str) -> None:
    """Send a colored Discord embed alert."""
    from tick_engine.settings import DISCORD_WEBHOOK_URL

    if not DISCORD_WEBHOOK_URL:
        return
    level = str(level or "INFO").upper()
    label = _LEVEL_LABEL.get(level, level.capitalize())
    color = _COLORS.get(level, _COLORS["INFO"])
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    plain = _strip_html(text)
    description = _trim(f"{plain}\n\n*{timestamp}*", 4096)

    def _send() -> None:
        _post_webhook(
            {
                "embeds": [
                    {
                        "title": f"Tick Engine — {label}",
                        "description": description,
                        "color": color,
                    }
                ]
            }
        )

    _spawn(_send)


def tg_flush(timeout: float = 12.0) -> None:
    """Wait for all pending Discord sends to finish."""
    global _pending_threads
    alive: list[threading.Thread] = []
    for t in _pending_threads:
        t.join(timeout=timeout)
        if t.is_alive():
            alive.append(t)
    _pending_threads = alive


# ---------------------------------------------------------------------------
# Tick-specific report helpers
# ---------------------------------------------------------------------------


def build_tick_report_text(
    title: str,
    *,
    conclusion: str,
    action: str | None = None,
    details: Iterable[object] | None = None,
    technical: Iterable[object] | None = None,
) -> str:
    body = [f"**{title}**", "", "**Summary**", _stringify(conclusion)]
    if action:
        body.extend(["", "**Action**", _stringify(action)])
    detail_text = _detail_block(details)
    if detail_text:
        body.extend(["", "**Details**", detail_text])
    technical_text = _detail_block(technical)
    if technical_text:
        body.extend(["", "**Technical**", technical_text])
    return "\n".join(body).strip()


def notify_tick(
    level: str,
    title: str,
    lines: Iterable[object] | None = None,
    *,
    body: str | None = None,
    throttle_key: str | None = None,
    throttle_seconds: int = 300,
) -> None:
    """Send a throttled operator-facing Discord embed without breaking ingest."""
    from tick_engine.settings import CACHE_DIR

    now = time.monotonic()
    if throttle_key:
        last = _last_sent.get(throttle_key, 0.0)
        if now - last < throttle_seconds:
            _system_event("throttled", f"key={throttle_key} | memory=yes")
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(throttle_key.encode("utf-8")).hexdigest()
            throttle_path = CACHE_DIR / f"tick_notify_throttle_{digest}.txt"
            wall_now = time.time()
            if throttle_path.exists():
                last_wall = float(throttle_path.read_text(encoding="ascii").strip() or "0")
                if wall_now - last_wall < throttle_seconds:
                    _system_event("throttled", f"key={throttle_key} | persistent=yes")
                    return
            throttle_path.write_text(str(wall_now), encoding="ascii")
        except Exception:
            logger.exception("tick Discord persistent throttle failed")
        finally:
            _last_sent[throttle_key] = now

    if body is None:
        detail_text = _detail_block(lines)
        if detail_text:
            body = "\n".join([f"**{title}**", "", "**Details**", detail_text])
        else:
            body = f"**{title}**"
    try:
        tg_alert(level, body)
    except Exception:
        logger.exception("tick Discord notification failed")


def notify_tick_report(
    level: str,
    title: str,
    *,
    conclusion: str,
    action: str | None = None,
    details: Iterable[object] | None = None,
    technical: Iterable[object] | None = None,
    throttle_key: str | None = None,
    throttle_seconds: int = 300,
) -> None:
    notify_tick(
        level,
        title,
        lines=None,
        body=build_tick_report_text(
            title,
            conclusion=conclusion,
            action=action,
            details=details,
            technical=technical,
        ),
        throttle_key=throttle_key,
        throttle_seconds=throttle_seconds,
    )


def flush_notifications(timeout: float = 12.0) -> None:
    try:
        tg_flush(timeout=timeout)
    except Exception:
        logger.exception("tick Discord flush failed")
