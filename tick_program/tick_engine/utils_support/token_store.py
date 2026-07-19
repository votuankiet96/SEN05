"""Local OAuth token cache for the cTrader/FTMO tick provider."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
DEFAULT_REFRESH_SAFETY_SECONDS = 24 * 60 * 60

TOKEN_KEYS = {
    "accessToken",
    "refreshToken",
    "tokenType",
    "expiresIn",
    "errorCode",
    "description",
}


def _default_cache_path() -> Path:
    from tick_engine.settings import TV_TOKEN_CACHE
    return TV_TOKEN_CACHE


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_utc_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def seconds_until_expiry(expires_at: Any) -> int | None:
    expiry = parse_utc_datetime(expires_at)
    if expiry is None:
        return None
    return int((expiry - datetime.now(UTC)).total_seconds())


def token_refresh_recommended(
    cache_or_expires_at: dict[str, Any] | Any,
    safety_seconds: int = DEFAULT_REFRESH_SAFETY_SECONDS,
) -> bool:
    expires_at = (
        cache_or_expires_at.get("expires_at_utc")
        if isinstance(cache_or_expires_at, dict)
        else cache_or_expires_at
    )
    remaining = seconds_until_expiry(expires_at)
    return remaining is not None and remaining <= int(safety_seconds)


def load_token_cache(path: Path | str | None = None) -> dict[str, Any]:
    token_path = Path(path) if path else _default_cache_path()
    if not token_path.exists():
        return {}
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid cTrader token cache JSON: {token_path}") from exc


def _expiry_from_payload(payload: dict[str, Any]) -> str | None:
    expires_in = payload.get("expiresIn")
    if not expires_in:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat()


def merge_token_payload(
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    account_id: int | None = None,
    trader_login: str | None = None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged["updated_at_utc"] = utc_now_iso()
    merged.setdefault("created_at_utc", merged["updated_at_utc"])
    for key in TOKEN_KEYS:
        if key in payload:
            merged[key] = payload[key]
    expires_at = _expiry_from_payload(payload)
    if expires_at:
        merged["expires_at_utc"] = expires_at
    for key, value in {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "ctidTraderAccountId": account_id,
        "traderLogin": trader_login,
    }.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def save_token_cache(
    payload: dict[str, Any],
    path: Path | str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    account_id: int | None = None,
    trader_login: str | None = None,
) -> Path:
    from tick_engine.settings import ensure_runtime_dirs
    from tick_engine.utils_support.lock_coord import JobLockConflict, exclusive_job_lock

    ensure_runtime_dirs()
    token_path = Path(path) if path else _default_cache_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 10.0
    while True:
        try:
            with exclusive_job_lock("token-cache", label="token cache writer"):
                existing = load_token_cache(token_path)
                merged = merge_token_payload(
                    payload,
                    existing=existing,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=scope,
                    account_id=account_id,
                    trader_login=trader_login,
                )
                tmp_path = token_path.with_name(
                    f".{token_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
                )
                try:
                    with tmp_path.open("w", encoding="utf-8") as handle:
                        json.dump(merged, handle, indent=2, sort_keys=True)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_path, token_path)
                finally:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                break
        except JobLockConflict:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for the cTrader token cache writer lock")
            time.sleep(0.1)
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token_path


def update_cached_account(
    account_id: int,
    trader_login: str | None = None,
    path: Path | str | None = None,
) -> Path:
    return save_token_cache(
        {}, path=path, account_id=account_id, trader_login=trader_login,
    )


def token_status(path: Path | str | None = None) -> dict[str, Any]:
    token_path = Path(path) if path else _default_cache_path()
    cache = load_token_cache(token_path)
    expires_at = cache.get("expires_at_utc")
    seconds_remaining = seconds_until_expiry(expires_at)
    is_expired = None
    if expires_at:
        is_expired = seconds_remaining is not None and seconds_remaining <= 0
    return {
        "path": str(token_path),
        "exists": token_path.exists(),
        "client_id_set": bool(cache.get("client_id")),
        "client_secret_set": bool(cache.get("client_secret")),
        "access_token_set": bool(cache.get("accessToken")),
        "refresh_token_set": bool(cache.get("refreshToken")),
        "account_id": cache.get("ctidTraderAccountId"),
        "trader_login": cache.get("traderLogin"),
        "redirect_uri": cache.get("redirect_uri"),
        "scope": cache.get("scope"),
        "expires_at_utc": expires_at,
        "seconds_until_expiry": seconds_remaining,
        "is_expired": is_expired,
        "refresh_recommended": token_refresh_recommended(cache),
    }
