"""Low-level, synchronous Discord webhook transport shared by all senders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiscordHttpResult:
    ok: bool
    status_code: int | None = None
    error: BaseException | None = None
    stage: str = "http"
    retry_after_seconds: float | None = None


def post_webhook_once(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    connect_timeout: float,
    read_timeout: float,
) -> DiscordHttpResult:
    """Perform exactly one verified request and return a structured result."""
    try:
        from core_engine.other.tls import ensure_system_truststore

        ensure_system_truststore()
        import requests
    except Exception as exc:
        return DiscordHttpResult(False, error=exc, stage="setup")

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=(connect_timeout, read_timeout),
            verify=True,
        )
    except Exception as exc:
        return DiscordHttpResult(False, error=exc, stage="http")

    retry_after = None
    if response.status_code == 429:
        try:
            retry_after = float(response.json().get("retry_after", 5))
        except Exception:
            retry_after = 5.0
    return DiscordHttpResult(
        response.status_code in (200, 204),
        status_code=int(response.status_code),
        retry_after_seconds=retry_after,
    )


__all__ = ["DiscordHttpResult", "post_webhook_once"]
