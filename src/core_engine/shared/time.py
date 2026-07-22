"""UTC timestamp parsing shared by health and process supervision."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_utc_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp, accepting ``Z`` and naive UTC values."""

    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
