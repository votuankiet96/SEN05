"""Small UTC helpers shared by runtime, health, and process supervision."""

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


def utc_iso() -> str:
    """Return the current timezone-aware UTC timestamp in ISO format."""

    return datetime.now(timezone.utc).isoformat()


def as_utc_timestamp(value: datetime) -> float:
    """Normalize a datetime-like value to an epoch timestamp in UTC."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.timestamp()


def future_cutoff_ts(*, tolerance_seconds: float = 60.0) -> float:
    """Upper timestamp bound used to reject implausible future candles."""

    return datetime.now(timezone.utc).timestamp() + float(tolerance_seconds)
