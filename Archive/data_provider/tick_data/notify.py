"""Discord notification helpers dedicated to the cTrader tick provider."""

from __future__ import annotations

import logging
import hashlib
import time
from collections.abc import Iterable
from typing import Any

from data_provider.paths import CACHE_DIR, ensure_runtime_dirs
from data_provider.common.notifications import tg_alert, tg_flush

logger = logging.getLogger(__name__)
_last_sent: dict[str, float] = {}


def _stringify(value: Any) -> str:
    text = str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


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


def build_tick_report_text(
    title: str,
    *,
    conclusion: str,
    action: str | None = None,
    details: Iterable[object] | None = None,
    technical: Iterable[object] | None = None,
) -> str:
    """Build a Discord-friendly report that non-technical operators can read."""
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


def notify_tick(
    level: str,
    title: str,
    lines: Iterable[object] | None = None,
    *,
    body: str | None = None,
    throttle_key: str | None = None,
    throttle_seconds: int = 300,
) -> None:
    """Send a clear operator-facing Discord embed without breaking ingest."""
    key = throttle_key
    now = time.monotonic()
    if key:
        last = _last_sent.get(key, 0.0)
        if now - last < throttle_seconds:
            return
        try:
            ensure_runtime_dirs()
            digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
            throttle_path = CACHE_DIR / f"tick_notify_throttle_{digest}.txt"
            wall_now = time.time()
            if throttle_path.exists():
                last_wall = float(throttle_path.read_text(encoding="ascii").strip() or "0")
                if wall_now - last_wall < throttle_seconds:
                    return
            throttle_path.write_text(str(wall_now), encoding="ascii")
        except Exception:
            logger.exception("tick Discord persistent throttle failed")
        finally:
            _last_sent[key] = now

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


def flush_notifications(timeout: float = 12.0) -> None:
    try:
        tg_flush(timeout=timeout)
    except Exception:
        logger.exception("tick Discord flush failed")
