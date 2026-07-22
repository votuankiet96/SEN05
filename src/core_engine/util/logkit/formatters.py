"""Log line formatting: single-line operator log records and console output."""

from __future__ import annotations

import logging
import re


def num(value) -> str:
    """Format integers with commas; return '-' for empty values."""
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


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


def clean(value) -> str:
    """Single-line text for operator logs."""
    if value is None:
        return "-"
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return " ".join(text.split()).strip() or "-"


def value_text(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int) and abs(value) >= 1000:
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return clean(value)


def operation_line(area: str, event: str, *details: str, **fields) -> str:
    """Format one horizontal operator log line.

    Output shape:
      AREA | Human event | key=value | key=value | detail
    """
    parts = [clean(area).upper(), clean(event)]
    for detail in details:
        detail_text = clean(detail)
        if detail_text and detail_text != "-":
            parts.append(detail_text)
    for key, value in fields.items():
        if value is None or value == "":
            continue
        safe_key = re.sub(r"[^A-Za-z0-9_]+", "_", str(key).strip().lower()).strip("_")
        if not safe_key:
            continue
        parts.append(f"{safe_key}={value_text(value)}")
    return " | ".join(parts)


_CONSOLE_SANITIZE_MAP = {
    "Ã¢Å“â€œ": "[OK]",
    "Ã¢Å“-": "[ERR]",
    "âœ—": "[ERR]",
    "Ã¢â€ Â»": "[RETRY]",
    "â†»": "[RETRY]",
    "Ã¢â€ â€™": "->",
    "Ã¢â‚¬â€": "-",
    "Ã¢â€°Ë†": "~",
    "â‰ˆ": "~",
    "Ã¢â‚¬Â¢": "-",
    "â€¢": "-",
    "Ã¢-â€¹": "[SKIP]",
    "Ã°Å¸â€Â": "[CHECK]",
    "ðŸ”": "[CHECK]",
    "Ã°Å¸â€Â§": "[FIX]",
    "ðŸ”§": "[FIX]",
    "Ã¢ÂÅ’": "[FAIL]",
    "Ã¢Å¡Â Ã¯Â¸Â": "[WARN]",
}


class ConsoleSanitizingFormatter(logging.Formatter):
    """Console-only formatter that normalizes mojibake/emoji markers."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for src, dst in _CONSOLE_SANITIZE_MAP.items():
            text = text.replace(src, dst)
        return text
