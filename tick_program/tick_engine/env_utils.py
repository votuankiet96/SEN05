"""Environment-variable parsing helpers and time utilities."""

from __future__ import annotations

import os
from datetime import datetime, timezone


def env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val not in ("0", "false", "no", "off")


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_int_list(name: str, default: list[int] | None = None) -> list[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default or [])
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


def utc_naive_now() -> datetime:
    """Return the current UTC time as a timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
