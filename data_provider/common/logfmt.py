"""Small fixed-width log helpers for data-provider console output."""

from __future__ import annotations

import logging
import re


COL_EVENT = 8
COL_PROGRESS = 7
COL_SYMBOL = 8
COL_TF = 4
COL_ACTION = 24
COL_AMOUNT = 30
COL_WINDOW = 43
COL_STATUS = 42
TABLE_EVENTS = {
    "EVENT", "PLAN", "TF", "PAIR", "RESULT", "WS", "WS_WARN", "REPLAY",
    "DB", "STAGE", "WARN", "ERROR", "CLEAN", "MISS", "STALE", "SYMBOL",
    "TF_SUM",
}


def num(value) -> str:
    """Format integers with commas; return '-' for empty values."""
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def cell(value, width: int, align: str = "left") -> str:
    """Fixed-width, single-line cell with conservative clipping."""
    text = "" if value is None or value == "" else str(value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(text) > width:
        text = text[: max(1, width - 1)] + "~"
    if align == "right":
        return text.rjust(width)
    return text.ljust(width)


def ts(value) -> str:
    """Compact timestamp for log windows."""
    if value is None:
        return "-"
    try:
        # pandas Timestamp supports tz_convert; datetime does not.
        if hasattr(value, "tz_convert") and getattr(value, "tzinfo", None) is not None:
            value = value.tz_convert("UTC")
        elif hasattr(value, "astimezone") and getattr(value, "tzinfo", None) is not None:
            from datetime import timezone

            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def window(start, end) -> str:
    if start is None and end is None:
        return "-"
    return f"from {ts(start)} to {ts(end)}"


def tv_status(status: str | None) -> str:
    """Translate verbose TradingView statuses into operator-friendly text."""
    if not status:
        return "-"
    text = str(status)
    if text.startswith("completed+more+"):
        match = re.search(r"completed\+more\+(\d+)", text)
        extra = num(match.group(1)) if match else "?"
        if "no_more" in text:
            return f"done; extra {extra}; max reached"
        return f"done; extra {extra}"
    if text.startswith("completed+no_more"):
        return "done; max reached"
    if text.startswith("completed"):
        return "done"
    if text.startswith("partial_timeout"):
        return "partial; timed out"
    if text.startswith("empty"):
        return "empty; no older bars"
    return text


def header() -> str:
    return make("EVENT", "PROG", "SYMBOL", "TF", "STEP", "COUNTS", "PERIOD", "RESULT")


def make(
    event: str,
    progress: str = "",
    symbol: str = "",
    tf: str = "",
    action: str = "",
    amount: str = "",
    range_: str = "",
    status: str = "",
) -> str:
    if event not in TABLE_EVENTS:
        parts = [cell(event, COL_EVENT).rstrip()]
        if progress:
            parts.append(str(progress))
        if action:
            parts.append(str(action))
        if amount:
            parts.append(str(amount))
        if range_:
            parts.append(str(range_))
        if status:
            parts.append(str(status))
        return " | ".join(parts)

    return (
        f"{cell(event, COL_EVENT)} | "
        f"{cell(progress, COL_PROGRESS)} | "
        f"{cell(symbol, COL_SYMBOL)} | "
        f"{cell(tf, COL_TF)} | "
        f"{cell(action, COL_ACTION)} | "
        f"{cell(amount, COL_AMOUNT)} | "
        f"{cell(range_, COL_WINDOW)} | "
        f"{cell(status, COL_STATUS)}"
    )


def log(
    logger: logging.Logger,
    event: str,
    progress: str = "",
    symbol: str = "",
    tf: str = "",
    action: str = "",
    amount: str = "",
    range_: str = "",
    status: str = "",
    level: int = logging.INFO,
) -> None:
    logger.log(level, make(event, progress, symbol, tf, action, amount, range_, status))
